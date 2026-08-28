from __future__ import annotations

import asyncio,logging,time
from contextlib import suppress
from enum import StrEnum
from typing import Any
from uuid import uuid5,NAMESPACE_URL

from maid_agent.actions.catalog import CATALOG,SAFE_TOOLS,ToolValidationError
from maid_agent.actions.client import ActionClient
from maid_agent.brain.context_builder import ContextBuilder
from maid_agent.brain.planner import Planner
from maid_agent.brain.strategy import DeterministicSurvivalPolicy,StrategyState,parse_strategy_decision,repair_prompt,strategy_prompt,StrategyDecision
from maid_agent.brain.tool_loop import resolve_references
from maid_agent.building.executor import BlueprintExecutor
from maid_agent.building.dsl import compile_dsl
from maid_agent.building.models import Blueprint
from maid_agent.capability.graph import CapabilityGraph
from maid_agent.control.events import EventBus
from maid_agent.goal.manager import GoalManager
from maid_agent.goal.models import Condition,Goal,GoalStatus,GoalType,Plan,PlanNodeKind,PlanStep,PlanUpdate,StepStatus
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.llm.provider import LLMProvider
from maid_agent.memory.context import MemoryIngestor
from maid_agent.memory.capability_gaps import CapabilityGap
from maid_agent.memory.store import MemoryStore
from maid_agent.metrics.scoreboard import Scoreboard
from maid_agent.persist.recovery import RuntimeRecovery
from maid_agent.protocol.models import ActionResult,ActionStatus,BridgeEvent,StateSnapshot
from maid_agent.reflection.queue import ReflectionEntry,ReflectionQueue
from maid_agent.rnd.handoff import HandoffBuilder
from maid_agent.rnd.service import RndService
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.skills.executor import SkillExecutor
from maid_agent.skills.store import SkillStore
from maid_agent.threat.analytics import ThreatAnalytics
from maid_agent.tokens.ledger import TokenLedger
from maid_agent.transport.ws_server import BridgeDisconnected,BridgeGateway

log=logging.getLogger(__name__)


class RuntimeMode(StrEnum):
    STOPPED="STOPPED";RUNNING="RUNNING";PAUSED="PAUSED";SAFE_IDLE="SAFE_IDLE";ERROR="ERROR"


class RuntimeController:
    TRANSIENT_CODES={"STUCK","PATH_NOT_FOUND","MOTION_BUSY","TRANSPORT_ERROR","TIMEOUT","BUSY","TARGET_NOT_VISIBLE"}
    HIGH_SIGNAL_EVENTS={"MAID_DEATH","HOSTILE_WAVE_DETECTED","BASE_DAMAGED","DAMAGE_TAKEN","ACTION_STUCK","LOW_HEALTH","RND_CYCLE_COMPLETED"}

    def __init__(self,*,gateway:BridgeGateway,store:MemoryStore,token_ledger:TokenLedger,skills:SkillStore,event_bus:EventBus,
                 verifier:PostconditionVerifier,provider:LLMProvider|None,rnd_trigger:RndTrigger,handoff_builder:HandoffBuilder,
                 scoreboard:Scoreboard,rnd_service:RndService|None=None,review_seconds:int=90,threats:ThreatAnalytics|None=None):
        self.gateway=gateway;self.store=store;self.token_ledger=token_ledger;self.skills=skills;self.event_bus=event_bus;self.verifier=verifier
        self.provider=provider;self.rnd_trigger=rnd_trigger;self.handoff_builder=handoff_builder;self.scoreboard=scoreboard;self.rnd_service=rnd_service;self.review_seconds=review_seconds
        self.action_client=ActionClient(gateway);self.goal_manager=GoalManager(store,verifier);self.capabilities=CapabilityGraph(store);self.planner=Planner(self.capabilities)
        self.fallback=DeterministicSurvivalPolicy(self.capabilities);self.reflections=ReflectionQueue();self.threats=threats or ThreatAnalytics(store)
        self.memory_ingestor=MemoryIngestor(store);self.context_builder=ContextBuilder(store,skills,self.threats)
        self.skill_executor=SkillExecutor(self.action_client,skills,verifier);self.blueprint_executor=BlueprintExecutor(self.action_client,store);self.recovery=RuntimeRecovery(store,verifier)
        self.strategy=self.recovery.restore_strategy();self.mode=RuntimeMode.STOPPED;self.active_plan:Plan|None=None;self.current_action:dict[str,Any]|None=None
        self._task:asyncio.Task[None]|None=None;self._event_task:asyncio.Task[None]|None=None;self._plan_task:asyncio.Task[None]|None=None;self._rnd_tasks:set[asyncio.Task[Any]]=set();self._rnd_task_cycles:dict[asyncio.Task[Any],Any]={};self._lifecycle_lock=asyncio.Lock()
        self._stop_event=asyncio.Event();self._last_snapshot_version=0;self._last_day=-1;self._last_review=0.0;self._desired_running=False;self._restored=False;self._strategic_review_requested=False;self.last_error="";self._pending_rnd_days:list[int]=[];self._next_rnd_retry_at=0.0
        self._recovery_goal:Goal|None=None;self._recovery_plan:Plan|None=None;self._recovery_waiting=False

    @property
    def current_day(self)->int|None:return self.gateway.latest_snapshot.day if self.gateway.latest_snapshot else None

    async def start(self)->None:
        self._desired_running=True;self._stop_event.clear()
        if not self._restored:
            goal,plan,notes=self.recovery.load_pending();self._recovery_goal=goal;self._recovery_plan=plan;self._recovery_waiting=goal is not None or plan is not None
            for note in notes:self.reflections.add(ReflectionEntry(source="recovery",code="RUNTIME_RESTORED",summary=note))
            self._restored=True
        self.mode=RuntimeMode.SAFE_IDLE if self._recovery_waiting or not self.gateway.connected else RuntimeMode.RUNNING
        if self._task is None or self._task.done():self._task=asyncio.create_task(self._run(),name="maid-autonomous-loop")
        if self._event_task is None or self._event_task.done():self._event_task=asyncio.create_task(self._event_loop(),name="maid-event-loop")
        self.store.set_runtime_state("desired_running",True);self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot())

    async def recover_rnd_cycles(self)->None:
        if self.rnd_service is None:return
        active={cycle.cycle_id for task,cycle in self._rnd_task_cycles.items() if not task.done()}
        for cycle in self.rnd_service.recover_interrupted_cycles(active):self._start_rnd_task(cycle)
        await asyncio.sleep(0)

    async def pause(self)->None:
        self._desired_running=False;self.mode=RuntimeMode.PAUSED
        if self._plan_task and not self._plan_task.done():
            self._plan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._plan_task
        await self.gateway.safe_idle("user_paused");self.store.set_runtime_state("desired_running",False);self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot())

    async def resume(self)->None:await self.start()

    async def stop(self)->None:
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self)->None:
        self._desired_running=False;self.mode=RuntimeMode.STOPPED;self._stop_event.set()
        for task in (self._plan_task,self._event_task,self._task):
            if task and not task.done():task.cancel()
        for task in (self._plan_task,self._event_task,self._task):
            if task:
                with suppress(asyncio.CancelledError):await task
        self._plan_task=self._event_task=self._task=None
        await self._cancel_rnd_tasks("Runtime 已停止，研发阶段与实际 Token 已保存")
        with suppress(Exception):await self.action_client.stop()
        await self.gateway.safe_idle("runtime_stopped");self.store.set_runtime_state("desired_running",False);self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot())

    async def _run(self)->None:
        while not self._stop_event.is_set():
            try:
                version,snapshot=await self.gateway.wait_for_snapshot(self._last_snapshot_version,timeout=20);self._last_snapshot_version=version;await self._on_snapshot(snapshot)
            except asyncio.TimeoutError:
                if self._desired_running and not self.gateway.connected:self.mode=RuntimeMode.SAFE_IDLE;self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot())
            except BridgeDisconnected:
                self.mode=RuntimeMode.SAFE_IDLE;self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot());await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception as exc:
                log.exception("autonomous loop failure");self.last_error=str(exc);self.mode=RuntimeMode.ERROR
                self.store.record_event(game_day=self.current_day or 0,game_tick=self.gateway.latest_snapshot.game_tick if self.gateway.latest_snapshot else 0,event_type="RUNTIME_ERROR",severity="ERROR",payload={"error":str(exc)})
                await self.gateway.safe_idle("runtime_error");self.event_bus.publish("RUNTIME_STATUS",self.status_snapshot());await asyncio.sleep(2)

    async def _event_loop(self)->None:
        while not self._stop_event.is_set():
            try:event=await self.gateway.next_event(timeout=10)
            except asyncio.TimeoutError:continue
            except asyncio.CancelledError:raise
            try:
                inserted=self.memory_ingestor.ingest_event(event,fallback_day=self.current_day or 0)
                if not inserted:continue
                self.scoreboard.event(event.event_type);strategic=self.threats.ingest_event(event,self.current_day or 0)
                if strategic or event.event_type in self.HIGH_SIGNAL_EVENTS:self._strategic_review_requested=True
                if event.event_type=="MAID_DEATH":
                    if self.goal_manager.current:self.goal_manager.current.status=GoalStatus.FAILED;self.store.save_model("goals","goal_id",str(self.goal_manager.current.goal_id),self.goal_manager.current,status=self.goal_manager.current.status)
                    self.mode=RuntimeMode.SAFE_IDLE
                self.event_bus.publish("MEMORY_EVENT_INGESTED",{"event_id":event.event_id,"event_type":event.event_type})
            except Exception as exc:log.exception("event ingest failed");self.reflections.add(ReflectionEntry(source="event",code="EVENT_INGEST_FAILED",summary=str(exc),context=event.model_dump(mode="json")))

    async def _on_snapshot(self,snapshot:StateSnapshot)->None:
        self.memory_ingestor.ingest_snapshot(snapshot);snapshot_strategic=self.threats.ingest_snapshot(snapshot)
        if snapshot_strategic:self._strategic_review_requested=True
        if snapshot.day!=self._last_day:
            self._last_day=snapshot.day;self.strategy.last_review_game_day=snapshot.day
            interval=max(1,self.rnd_trigger.cycle_days)
            due_day=snapshot.day-(snapshot.day%interval)
            if due_day>0 and due_day not in self._pending_rnd_days:
                with self.store.connection() as conn:
                    latest_trigger=int(conn.execute("SELECT COALESCE(MAX(trigger_day),0) FROM rnd_cycles").fetchone()[0])
                    already_recorded=conn.execute("SELECT 1 FROM rnd_cycles WHERE trigger_day=?",(due_day,)).fetchone() is not None
                if due_day>latest_trigger and not already_recorded:self._pending_rnd_days.append(due_day)
        self._start_pending_rnd_if_possible()
        if self._recovery_waiting:
            if not self.gateway.resync_ready:return
            goal,plan,notes=self.recovery.revalidate(self._recovery_goal,self._recovery_plan,snapshot,cached_results=self.gateway.cached_results())
            self.goal_manager.restore(goal);self.active_plan=plan;self._recovery_goal=None;self._recovery_plan=None;self._recovery_waiting=False
            for note in notes:self.reflections.add(ReflectionEntry(source="recovery",code="STATE_RESYNC_REVALIDATED",summary=note))
            self.event_bus.publish("RECOVERY_REVALIDATED",{"goal_id":str(goal.goal_id) if goal else None,"plan_id":str(plan.plan_id) if plan else None,"notes":notes})
        if self._desired_running and self.gateway.connected and self.mode==RuntimeMode.SAFE_IDLE:self.mode=RuntimeMode.RUNNING
        if self.mode!=RuntimeMode.RUNNING:return

        status=self.goal_manager.verify(snapshot)
        if status in {GoalStatus.SUCCESS,GoalStatus.FAILED,GoalStatus.BLOCKED}:
            self.scoreboard.goal(str(getattr(status,"value",status)))
            self._strategic_review_requested=True
            if status==GoalStatus.SUCCESS:
                completed=self.goal_manager.current;parent=self.goal_manager.resume_parent_if_ready()
                if parent and completed and completed.resume_plan_id:
                    raw=self.store.load_plan(str(completed.resume_plan_id));self.active_plan=Plan.model_validate(raw) if raw else None
                    if self.active_plan:self.active_plan.status="PAUSED"
                elif parent is None:self.active_plan=None
            else:self.active_plan=None

        if self._plan_task and not self._plan_task.done():return
        if self.active_plan and self.goal_manager.current and self.goal_manager.current.status in {GoalStatus.ACTIVE,GoalStatus.PAUSED,GoalStatus.NEEDS_REVALIDATION} and self.active_plan.status in {"PENDING","PAUSED","RUNNING","NEEDS_REVALIDATION"}:
            self.goal_manager.current.status=GoalStatus.ACTIVE;self._plan_task=asyncio.create_task(self._execute_plan(self.active_plan),name=f"plan-{self.active_plan.plan_id}");return

        if self.active_plan is None and self.goal_manager.current and self.goal_manager.current.status==GoalStatus.ACTIVE:
            continuation=self._continuation_plan(snapshot)
            if continuation is not None:
                self.active_plan=continuation;self._save_plan(continuation)
                self._plan_task=asyncio.create_task(self._execute_plan(continuation),name=f"plan-{continuation.plan_id}")
                return

        now=time.monotonic()
        if not self._strategic_review_requested and now-self._last_review<self.review_seconds:return
        decision=await self._decide(snapshot)
        self._record_capability_gap(decision,snapshot)
        if decision.keep_current_goal and self.goal_manager.current and self.active_plan:
            applied=self.planner.apply_updates(self.active_plan,decision.plan_updates)
            if applied:
                self._save_plan(self.active_plan)
                self.event_bus.publish("PLAN_UPDATED",{"plan_id":str(self.active_plan.plan_id),"step_ids":applied,"revision":self.active_plan.revision})
            self.strategy.decision_summary=decision.decision_summary;self.strategy.evidence=decision.evidence
            self.store.save_strategy_state(self.strategy)
            self._last_review=now;self._strategic_review_requested=False;return
        if decision.keep_current_goal:
            decision=self.fallback.decide(snapshot,self.context_builder.build(
                snapshot=snapshot,goal=self.goal_manager.current,plan=self.active_plan,
                strategy=self.strategy,reflections=self.reflections.recent(),
            ))
        goal,plan=self.planner.from_decision(decision,game_day=snapshot.day,snapshot=snapshot);goal.source="runtime_llm" if self.provider else "fallback"
        self.goal_manager.set(goal);self.active_plan=plan;self.strategy.current_focus=goal.objective;self.strategy.decision_summary=decision.decision_summary;self.strategy.evidence=decision.evidence;self.strategy.threat_posture=self.threats.context_summary(snapshot.day)["risk_level"]
        self.store.save_strategy_state(self.strategy);self.store.save_model("plans","plan_id",str(plan.plan_id),plan,status=plan.status)
        self.event_bus.publish("DECISION",{"goal_id":str(goal.goal_id),"objective":goal.objective,"decision_summary":decision.decision_summary,"evidence":decision.evidence})
        self._last_review=now;self._strategic_review_requested=False;self._plan_task=asyncio.create_task(self._execute_plan(plan),name=f"plan-{plan.plan_id}")

    async def _decide(self,snapshot:StateSnapshot)->StrategyDecision:
        context=self.context_builder.build(snapshot=snapshot,goal=self.goal_manager.current,plan=self.active_plan,strategy=self.strategy,reflections=self.reflections.recent())
        if self.provider is None:return self.fallback.decide(snapshot,context)
        try:
            response=await self.provider.complete(strategy_prompt(context=context,state=self.strategy),model_role="runtime_strategy",purpose="planning",response_schema=StrategyDecision.model_json_schema())
            try:return parse_strategy_decision(response.content)
            except ValueError as first:
                repair=await self.provider.complete(repair_prompt(response.content,str(first)),model_role="runtime_strategy_repair",purpose="repair",response_schema=StrategyDecision.model_json_schema())
                return parse_strategy_decision(repair.content)
        except Exception as exc:
            self.reflections.add(ReflectionEntry(source="strategy_provider",code="PROVIDER_FALLBACK",summary="战略模型调用或结构修复失败，已使用确定性安全策略。",context={"error":str(exc)}))
            self.store.record_event(game_day=snapshot.day,game_tick=snapshot.game_tick,event_type="LLM_FALLBACK",severity="WARN",payload={"error":str(exc)})
            return self.fallback.decide(snapshot,context)

    async def _execute_plan(self,plan:Plan)->None:
        plan.status="RUNNING";goal=self.goal_manager.current
        if goal:
            goal.status=GoalStatus.ACTIVE
            self.store.save_model("goals","goal_id",str(goal.goal_id),goal,status=goal.status)
        snapshot=self.gateway.latest_snapshot
        if snapshot:
            plan.checkpoint.setdefault("started_game_tick",snapshot.game_tick)
            plan.checkpoint.setdefault("started_dimension",snapshot.dimension)
        previous_data=dict(plan.checkpoint.get("previous_data") or {})
        self._save_plan(plan)
        try:
            signal,previous_data=await self._execute_nodes(plan,plan.steps,previous_data,(),top_level=True)
            plan.checkpoint["previous_data"]=previous_data
            if signal in {"DONE","GOAL_SUCCESS"}:
                if signal=="GOAL_SUCCESS":self._mark_pending(plan.steps,StepStatus.SKIPPED)
                plan.status="DONE"
            elif signal=="MATERIAL":
                return
            elif signal in {"PAUSED","PREEMPTED","ABORTED","TIMEOUT","BLOCKED"}:
                plan.status=signal
            elif plan.status=="RUNNING":
                plan.status="BLOCKED"
            self._save_plan(plan)
            latest=self.gateway.latest_snapshot
            replacement_active=(
                self.active_plan is not None and self.active_plan is not plan
                and bool(plan.checkpoint.get("replaced_by_plan_id"))
            )
            if latest and not replacement_active:
                goal_status=self.goal_manager.verify(latest)
                if plan.status=="BLOCKED" and goal_status==GoalStatus.ACTIVE:
                    failed=next((node for node in plan.iter_nodes() if node.last_error_code),None)
                    self.goal_manager.mark_blocked(failed.last_error_code if failed else "PLAN_BLOCKED",game_tick=latest.game_tick)
                elif plan.status in {"DONE","PREEMPTED","ABORTED","TIMEOUT"} and goal_status==GoalStatus.ACTIVE:
                    self._strategic_review_requested=True
            self.event_bus.publish("PLAN_STATUS",{
                "plan_id":str(plan.plan_id),"status":plan.status,
                "steps":[step.model_dump(mode="json") for step in plan.steps],
            })
            if plan.status in {"DONE","BLOCKED","PREEMPTED","ABORTED","TIMEOUT"} and self.active_plan is plan:
                self.active_plan=None
        except asyncio.CancelledError:
            self._mark_running(plan.steps,StepStatus.PAUSED,"USER_PAUSED")
            plan.status="PAUSED";self._save_plan(plan);raise
        finally:
            self.current_action=None

    async def _execute_nodes(
        self,plan:Plan,nodes:list[PlanStep],previous_data:dict[str,Any],path:tuple[int,...],*,top_level:bool=False
    )->tuple[str,dict[str,Any]]:
        for index,node in enumerate(nodes):
            node_path=path+(index,);plan.checkpoint["current_path"]=list(node_path)
            if top_level:
                plan.current_step_index=index
            self._save_plan(plan)
            interrupt=self._plan_interrupt(plan,node)
            if interrupt:return interrupt,previous_data
            if node.status in {StepStatus.DONE,StepStatus.SKIPPED}:
                if node.status==StepStatus.DONE and node.result_data:previous_data=node.result_data
                continue
            snapshot=self.gateway.latest_snapshot
            if snapshot is None:
                node.status=StepStatus.ABORTED;node.last_error_code="NO_SNAPSHOT";return "ABORTED",previous_data
            if node.preconditions and not self.verifier.all(node.preconditions,snapshot,self._last_action_result(plan)):
                node.status=StepStatus.BLOCKED;node.last_error_code="PRECONDITION_FAILED";return "BLOCKED",previous_data
            if node.kind in {PlanNodeKind.ACTION,PlanNodeKind.WAIT}:
                signal,previous_data=await self._execute_action_step(plan,node,previous_data)
                if signal!="DONE":return signal,previous_data
                if self.goal_manager.current and self.goal_manager.verify(
                    self.gateway.latest_snapshot or snapshot,self._last_action_result(plan)
                )==GoalStatus.SUCCESS:
                    return "GOAL_SUCCESS",previous_data
            elif node.kind==PlanNodeKind.IF:
                node.status=StepStatus.RUNNING
                selected=node.then_steps if self._flow_condition(node.condition,plan) else node.else_steps
                signal,previous_data=await self._execute_nodes(plan,selected,previous_data,node_path)
                if signal not in {"DONE","GOAL_SUCCESS"}:return signal,previous_data
                node.status=StepStatus.DONE;node.result_data={"branch":"then" if selected is node.then_steps else "else"}
                if signal=="GOAL_SUCCESS":return signal,previous_data
            elif node.kind==PlanNodeKind.BRANCH:
                node.status=StepStatus.RUNNING;selected=None;selected_index=-1
                for branch_index,branch in enumerate(node.branches):
                    if self._flow_condition(branch.condition,plan):selected=branch.steps;selected_index=branch_index;break
                if selected is None:selected=node.else_steps
                if selected:
                    signal,previous_data=await self._execute_nodes(plan,selected,previous_data,node_path)
                    if signal not in {"DONE","GOAL_SUCCESS"}:return signal,previous_data
                    if signal=="GOAL_SUCCESS":return signal,previous_data
                node.status=StepStatus.DONE;node.result_data={"branch_index":selected_index}
            elif node.kind in {PlanNodeKind.REPEAT,PlanNodeKind.WHILE,PlanNodeKind.UNTIL}:
                signal,previous_data=await self._execute_loop(plan,node,previous_data,node_path)
                if signal!="DONE":return signal,previous_data
            elif node.kind==PlanNodeKind.ABORT:
                node.status=StepStatus.ABORTED;node.last_error_code=str(node.args.get("code") or "PLAN_ABORT")
                return "ABORTED",previous_data
            elif node.kind==PlanNodeKind.PAUSE:
                node.status=StepStatus.PAUSED;node.last_error_code=str(node.args.get("code") or "PLAN_PAUSE")
                return "PAUSED",previous_data
            plan.checkpoint["previous_data"]=previous_data
            if top_level:plan.current_step_index=index+1
            if await self._maybe_adjust_plan(plan):
                return "ABORTED",previous_data
        return "DONE",previous_data

    async def _execute_loop(
        self,plan:Plan,node:PlanStep,previous_data:dict[str,Any],path:tuple[int,...]
    )->tuple[str,dict[str,Any]]:
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:return "ABORTED",previous_data
        loop_states=plan.checkpoint.setdefault("loop_states",{})
        state=loop_states.setdefault(str(node.step_id),{"iteration":0,"started_game_tick":snapshot.game_tick})
        iteration=int(state.get("iteration",0));started=int(state.get("started_game_tick",snapshot.game_tick))
        node.status=StepStatus.RUNNING
        while iteration<node.max_iterations:
            snapshot=self.gateway.latest_snapshot
            if snapshot is None:
                node.status=StepStatus.ABORTED;node.last_error_code="NO_SNAPSHOT";return "ABORTED",previous_data
            interrupt=self._plan_interrupt(plan,node)
            if interrupt:return interrupt,previous_data
            if snapshot.game_tick-started>node.max_duration_ticks:
                node.status=StepStatus.FAILED;node.last_error_code="LOOP_TIMEOUT";return "TIMEOUT",previous_data
            exit_met=self._flow_condition(node.exit_condition,plan)
            if exit_met:
                node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"exit_condition":True}
                loop_states.pop(str(node.step_id),None);return "DONE",previous_data
            if node.kind==PlanNodeKind.WHILE and not self._flow_condition(node.condition,plan):
                node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"while_condition":False}
                loop_states.pop(str(node.step_id),None);return "DONE",previous_data
            if node.kind==PlanNodeKind.REPEAT and iteration>=node.repeat_count:
                node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"bounded_completion":True}
                loop_states.pop(str(node.step_id),None);return "DONE",previous_data
            if iteration>0:self._reset_nodes(node.body)
            signal,previous_data=await self._execute_nodes(plan,node.body,previous_data,path+(iteration,))
            if signal!="DONE":return signal,previous_data
            iteration+=1;state["iteration"]=iteration;self._save_plan(plan)
            if node.kind==PlanNodeKind.REPEAT and iteration>=node.repeat_count:
                node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"bounded_completion":True}
                loop_states.pop(str(node.step_id),None);return "DONE",previous_data
        if node.kind==PlanNodeKind.REPEAT:
            node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"bounded_completion":True}
            loop_states.pop(str(node.step_id),None);return "DONE",previous_data
        if self._flow_condition(node.exit_condition,plan):
            node.status=StepStatus.DONE;node.result_data={"iterations":iteration,"exit_condition":True}
            loop_states.pop(str(node.step_id),None);return "DONE",previous_data
        node.status=StepStatus.FAILED;node.last_error_code="LOOP_LIMIT_REACHED"
        return "BLOCKED",previous_data

    async def _execute_action_step(
        self,plan:Plan,step:PlanStep,previous_data:dict[str,Any]
    )->tuple[str,dict[str,Any]]:
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:return "ABORTED",previous_data
        if self._can_use_state_postcondition(step) and self.verifier.all(step.success_conditions,snapshot):
            step.status=StepStatus.DONE;step.side_effect_verified=True
            step.result_data={"revalidated_from_state":True};return "DONE",step.result_data
        try:
            args=resolve_references(step.args,previous=previous_data,context={"snapshot":snapshot.model_dump(mode="json")})
            args=CATALOG.validate(step.tool,args)
        except (ValueError,ToolValidationError) as exc:
            step.status=StepStatus.BLOCKED;step.last_error_code=getattr(exc,"code","UNRESOLVED_REFERENCE")
            self._reflect_step(step,str(exc));return "BLOCKED",previous_data
        seen_codes:dict[str,int]={}
        while step.retry_count<=step.max_retries:
            interrupt=self._plan_interrupt(plan,step)
            if interrupt:return interrupt,previous_data
            step.status=StepStatus.RUNNING
            if not step.request_id:
                step.request_id=str(uuid5(NAMESPACE_URL,f"plan:{plan.plan_id}:step:{step.step_id}:attempt:{step.retry_count}"))
            self.current_action={"tool":step.tool,"args":args,"step_id":str(step.step_id),"request_id":step.request_id}
            self.event_bus.publish("CURRENT_ACTION",self.current_action);self._save_plan(plan)
            before_version=self.gateway.snapshot_version;result=None
            try:
                result=await self._execute_tool(step.tool,args,step.timeout_ticks,step.request_id)
            except Exception as exc:
                code="TRANSPORT_ERROR"
                self.reflections.add(ReflectionEntry(source="action",code=code,summary=str(exc),context={"tool":step.tool,"args":args}))
            else:
                code=result.code;plan.checkpoint["last_action_result"]=result.model_dump(mode="json")
                self.scoreboard.action(str(getattr(result.status,"value",result.status)),result.code)
                if result.status==ActionStatus.PREEMPTED:
                    step.status=StepStatus.PREEMPTED;step.last_error_code=result.code;return "PREEMPTED",previous_data
                if result.status==ActionStatus.CANCELLED:
                    step.status=StepStatus.CANCELLED;step.last_error_code=result.code
                    return ("PAUSED" if self.mode==RuntimeMode.PAUSED else "ABORTED"),previous_data
                if result.ok:
                    latest=await self._fresh_snapshot(before_version,snapshot)
                    if not step.success_conditions or self.verifier.all(step.success_conditions,latest,result):
                        step.status=StepStatus.DONE;step.result_data=result.data;step.side_effect_verified=True
                        self.store.record_event(game_day=latest.day,game_tick=latest.game_tick,event_type="ACTION_SUCCEEDED",severity="INFO",payload={"tool":step.tool,"code":result.code,"request_id":step.request_id})
                        self.current_action=None;self._save_plan(plan);return "DONE",result.data
                    code="POSTCONDITION_FAILED"
                elif self._can_use_state_postcondition(step):
                    latest=await self._fresh_snapshot(before_version,snapshot)
                    if self.verifier.all(step.success_conditions,latest):
                        step.status=StepStatus.DONE;step.result_data={"revalidated_from_state":True,"bridge_code":result.code};step.side_effect_verified=True
                        self.current_action=None;self._save_plan(plan);return "DONE",step.result_data
            seen_codes[code]=seen_codes.get(code,0)+1;step.last_error_code=code
            current=self.gateway.latest_snapshot or snapshot;goal=self.goal_manager.current
            if code=="TARGET_NOT_VISIBLE" and goal:
                target=str(goal.metadata.get("capability_target") or "")
                if target in self.capabilities.WORKSTATION_BLOCKS:self.capabilities.forget_stale_workstation(current,target)
            self.store.record_event(game_day=current.day,game_tick=current.game_tick,event_type="ACTION_FAILED",severity="WARN",payload={"tool":step.tool,"code":code,"request_id":step.request_id,"attempt":step.retry_count})
            if code=="TARGET_GONE":
                step.status=StepStatus.ABORTED;return "ABORTED",previous_data
            if result and step.tool in {"build_blueprint","build_dsl"} and code=="NO_MATERIAL":
                if self._create_material_child(plan,result.data.get("missing_materials") or {},snapshot):
                    step.status=StepStatus.PAUSED;plan.status="PAUSED";return "MATERIAL",previous_data
            step.retry_count+=1;step.request_id=None
            if step.retry_count>step.max_retries or seen_codes[code]>=2 or code not in self.TRANSIENT_CODES:
                step.status=StepStatus.FAILED;self._reflect_step(step,f"步骤失败：{code}")
                self.current_action=None;self._save_plan(plan);return "BLOCKED",previous_data
            await asyncio.sleep(min(2.0,.5*step.retry_count))
        return "BLOCKED",previous_data

    def _plan_interrupt(self,plan:Plan,node:PlanStep)->str|None:
        if self.mode!=RuntimeMode.RUNNING:
            node.status=StepStatus.PAUSED;node.last_error_code="USER_PAUSED";return "PAUSED"
        if not self.gateway.connected:
            node.status=StepStatus.ABORTED;node.last_error_code="AGENT_DISCONNECTED";return "ABORTED"
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:
            node.status=StepStatus.ABORTED;node.last_error_code="WORLD_UNAVAILABLE";return "ABORTED"
        if snapshot.health<=0:
            node.status=StepStatus.ABORTED;node.last_error_code="MAID_DEAD";return "ABORTED"
        if snapshot.dimension!=plan.checkpoint.get("started_dimension",snapshot.dimension):
            node.status=StepStatus.ABORTED;node.last_error_code="WORLD_CHANGED";return "ABORTED"
        if snapshot.reflex_state not in {"","NONE"}:
            node.status=StepStatus.PREEMPTED;node.last_error_code=f"REFLEX_{snapshot.reflex_state}";return "PREEMPTED"
        if snapshot.health<=max(2.0,snapshot.max_health*.1) and snapshot.nearest_hostile() is not None:
            node.status=StepStatus.PREEMPTED;node.last_error_code="LIFE_DANGER";return "PREEMPTED"
        started=int(plan.checkpoint.get("started_game_tick",snapshot.game_tick))
        if snapshot.game_tick-started>plan.max_duration_ticks:
            node.status=StepStatus.FAILED;node.last_error_code="PLAN_TIMEOUT";return "TIMEOUT"
        return None

    def _flow_condition(self,condition:Condition|None,plan:Plan)->bool:
        if condition is None:return False
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:return False
        return self.verifier.evaluate(condition,snapshot,self._last_action_result(plan))

    @staticmethod
    def _last_action_result(plan:Plan)->ActionResult|None:
        raw=plan.checkpoint.get("last_action_result")
        if not isinstance(raw,dict):return None
        try:return ActionResult.model_validate(raw)
        except Exception:return None

    @classmethod
    def _reset_nodes(cls,nodes:list[PlanStep])->None:
        for node in nodes:
            node.status=StepStatus.PENDING;node.retry_count=0;node.request_id=None;node.last_error_code=None
            node.result_data={};node.side_effect_verified=False
            cls._reset_nodes(node.then_steps);cls._reset_nodes(node.else_steps);cls._reset_nodes(node.body)
            for branch in node.branches:cls._reset_nodes(branch.steps)

    @classmethod
    def _mark_pending(cls,nodes:list[PlanStep],status:StepStatus)->None:
        for node in nodes:
            if node.status in {StepStatus.PENDING,StepStatus.PAUSED,StepStatus.NEEDS_REVALIDATION}:node.status=status
            cls._mark_pending(node.then_steps,status);cls._mark_pending(node.else_steps,status);cls._mark_pending(node.body,status)
            for branch in node.branches:cls._mark_pending(branch.steps,status)

    @classmethod
    def _mark_running(cls,nodes:list[PlanStep],status:StepStatus,code:str)->None:
        for node in nodes:
            if node.status==StepStatus.RUNNING:node.status=status;node.last_error_code=code
            cls._mark_running(node.then_steps,status,code);cls._mark_running(node.else_steps,status,code);cls._mark_running(node.body,status,code)
            for branch in node.branches:cls._mark_running(branch.steps,status,code)

    async def _maybe_adjust_plan(self,plan:Plan)->bool:
        if not self.provider or not self._strategic_review_requested:return False
        version=self.gateway.snapshot_version
        if plan.checkpoint.get("last_adjust_snapshot_version")==version:return False
        plan.checkpoint["last_adjust_snapshot_version"]=version
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:return False
        decision=await self._decide(snapshot)
        self._record_capability_gap(decision,snapshot)
        if decision.keep_current_goal:
            applied=self.planner.apply_updates(plan,decision.plan_updates) if decision.plan_updates else []
            if applied:
                self.event_bus.publish("PLAN_UPDATED",{"plan_id":str(plan.plan_id),"step_ids":applied,"revision":plan.revision})
            self.strategy.decision_summary=decision.decision_summary;self.strategy.evidence=decision.evidence
            self.store.save_strategy_state(self.strategy);self._save_plan(plan)
            self._last_review=time.monotonic();self._strategic_review_requested=False
            return False

        # A strategic replacement ends only unfinished plan nodes. Completed Minecraft
        # effects remain real and are deliberately not rolled back.
        new_goal,new_plan=self.planner.from_decision(decision,game_day=snapshot.day,snapshot=snapshot)
        new_goal.source="runtime_llm"
        self._mark_pending(plan.steps,StepStatus.ABORTED)
        self._mark_running(plan.steps,StepStatus.ABORTED,"GOAL_REPLACED")
        plan.status="ABORTED"
        plan.checkpoint["replacement_reason"]=decision.decision_summary
        plan.checkpoint["replaced_by_plan_id"]=str(new_plan.plan_id)
        self._save_plan(plan)
        self.goal_manager.replace(
            new_goal,reason=decision.decision_summary,replacement_plan_id=str(new_plan.plan_id)
        )
        self.active_plan=new_plan;self._save_plan(new_plan)
        self.strategy.current_focus=new_goal.objective
        self.strategy.decision_summary=decision.decision_summary;self.strategy.evidence=decision.evidence
        self.store.save_strategy_state(self.strategy)
        self.event_bus.publish("GOAL_REPLACED",{
            "old_plan_id":str(plan.plan_id),"new_goal_id":str(new_goal.goal_id),
            "new_plan_id":str(new_plan.plan_id),"objective":new_goal.objective,
        })
        self._last_review=time.monotonic();self._strategic_review_requested=False
        return True

    def _record_capability_gap(self,decision:StrategyDecision,snapshot:StateSnapshot)->dict[str,Any]|None:
        if decision.capability_gap is None:return None
        gap=CapabilityGap.from_draft(
            decision.capability_gap,game_day=snapshot.day,game_tick=snapshot.game_tick,
        )
        stored=self.store.record_capability_gap(gap)
        self.store.record_event(
            game_day=snapshot.day,game_tick=snapshot.game_tick,event_type="CAPABILITY_GAP_RECORDED",
            severity="INFO",payload={
                **stored,
                "rnd_role":"background_only_not_required_direction",
                "attempt_order":"mature_capability_then_temporary_plan_then_adjustment",
            },
        )
        self.event_bus.publish("CAPABILITY_GAP",stored)
        return stored

    async def _execute_tool(self,tool:str,args:dict[str,Any],timeout_ticks:int,request_id:str)->ActionResult:
        if tool=="run_skill":
            spec=self.skills.get(str(args["skill_id"]),int(args.get("version") or 0) or None,production_only=True)
            if spec is None:return ActionResult(request_id=request_id,action_id=request_id,status=ActionStatus.FAILED,code="SKILL_NOT_ACTIVE",data={},world_delta={})
            results,ok,code=await self.skill_executor.execute(spec,dict(args.get("parameters") or {}),lambda:self.gateway.latest_snapshot)
            return ActionResult(request_id=request_id,action_id=request_id,status=ActionStatus.SUCCESS if ok else ActionStatus.FAILED,code=code,data={"skill_id":spec.skill_id,"version":spec.version,"results":[r.model_dump(mode="json") for r in results]},world_delta={})
        if tool=="build_blueprint":return await self.blueprint_executor.execute(dict(args["blueprint"]),dict(args["origin"]),int(args.get("rotation",0)),lambda:self.gateway.latest_snapshot)
        if tool=="build_dsl":
            blueprint=compile_dsl(dict(args["dsl"]))
            return await self.blueprint_executor.execute(blueprint.model_dump(mode="json"),dict(args["origin"]),int(args.get("rotation",0)),lambda:self.gateway.latest_snapshot)
        return await self.action_client.execute(tool,args,timeout_ticks=timeout_ticks,request_id=request_id)

    @staticmethod
    def _can_use_state_postcondition(step:PlanStep)->bool:
        return bool(step.success_conditions) and step.tool in {"pickup_nearby","craft","smelt","equip","eat"}

    async def _fresh_snapshot(self,before_version:int,fallback:StateSnapshot)->StateSnapshot:
        if self.gateway.snapshot_version>before_version and self.gateway.latest_snapshot:return self.gateway.latest_snapshot
        try:_,snapshot=await self.gateway.wait_for_snapshot(before_version,timeout=3);return snapshot
        except Exception:return self.gateway.latest_snapshot or fallback

    def _create_material_child(self,parent_plan:Plan,missing:dict[str,int],snapshot:StateSnapshot)->bool:
        if not missing or not self.goal_manager.current:return False
        item_id,count=max(missing.items(),key=lambda item:item[1]);parent=self.goal_manager.current;parent_plan.status="PAUSED";parent_plan.checkpoint["missing_materials"]=missing;self._save_plan(parent_plan)
        child=Goal(type=GoalType.ACQUIRE_ITEM,objective=f"为建筑补充 {count} 个 {item_id}",priority=min(100,parent.priority+5),success_conditions=[Condition(type="ITEM_COUNT",args={"item_id":item_id,"count":snapshot.item_count(item_id)+count})],created_game_day=snapshot.day,source="building",parent_goal_id=parent.goal_id,resume_plan_id=parent_plan.plan_id,metadata={"missing_materials":missing})
        resolution=self.capabilities.resolve_material(item_id);target=resolution.get("capability")
        steps=self.capabilities.acquisition_steps(snapshot,item_id,count)
        child.metadata["capability_target"]=target
        child_plan=Plan(goal_id=child.goal_id,steps=steps,created_game_day=snapshot.day);self.goal_manager.create_child(child,parent,parent_status=GoalStatus.PAUSED_MATERIALS);self.store.save_model("plans","plan_id",str(child_plan.plan_id),child_plan,status=child_plan.status);self.active_plan=child_plan
        self.event_bus.publish("BUILD_MATERIAL_SUBGOAL",{"parent_goal_id":str(parent.goal_id),"child_goal_id":str(child.goal_id),"missing":missing});return True

    def _continuation_plan(self,snapshot:StateSnapshot)->Plan|None:
        goal=self.goal_manager.current
        if goal is None:return None
        if goal.source=="building":
            missing=dict(goal.metadata.get("missing_materials") or {})
            if not missing:return None
            item_id,count=max(missing.items(),key=lambda row:row[1])
            remaining=max(1,int(count))
            for condition in goal.success_conditions:
                if condition.type=="ITEM_COUNT" and condition.args.get("item_id")==item_id:
                    remaining=max(1,int(condition.args.get("count",1))-snapshot.item_count(item_id))
                    break
            steps=self.capabilities.acquisition_steps(snapshot,item_id,remaining)
        else:
            target=goal.metadata.get("capability_target")
            if not target:return None
            if target=="food":steps=self.capabilities.explore_steps(snapshot,"继续探索新的食物来源")
            else:steps=self.capabilities.next_steps(snapshot,str(target))
        if not steps:return None
        plan=Plan(goal_id=goal.goal_id,steps=steps,created_game_day=snapshot.day)
        self.event_bus.publish("GOAL_CONTINUED",{"goal_id":str(goal.goal_id),"plan_id":str(plan.plan_id),"source":goal.source})
        return plan

    def _reflect_step(self,step:PlanStep,summary:str)->None:self.reflections.add(ReflectionEntry(source="plan",code=step.last_error_code or "PLAN_BLOCKED",summary=summary,context={"step":step.model_dump(mode="json")}))
    def _save_plan(self,plan:Plan)->None:self.store.save_model("plans","plan_id",str(plan.plan_id),plan,status=plan.status)

    def _start_pending_rnd_if_possible(self)->None:
        if not self._pending_rnd_days or any(not task.done() for task in self._rnd_tasks):return
        now=time.monotonic()
        if now<self._next_rnd_retry_at:return
        self._next_rnd_retry_at=now+5
        if self._create_rnd_cycle_if_due(self._pending_rnd_days[0]):self._pending_rnd_days.pop(0)

    def _create_rnd_cycle_if_due(self,day:int)->bool:
        try:cycle=self.rnd_trigger.create_if_due(day)
        except Exception as exc:
            log.exception("R&D cycle creation failed")
            self.event_bus.publish("RND_STATUS",{"trigger_day":day,"status":"FAILED","code":"CREATE_FAILED","error":str(exc)})
            with self.store.connection() as conn:
                return conn.execute("SELECT 1 FROM rnd_cycles WHERE trigger_day=?",(day,)).fetchone() is not None
        if cycle is None:
            with self.store.connection() as conn:
                return conn.execute("SELECT 1 FROM rnd_cycles WHERE trigger_day=?",(day,)).fetchone() is not None
        try:
            self.handoff_builder.prepare_input(cycle,self.strategy,sorted(SAFE_TOOLS));self.handoff_builder.create_default_output(cycle)
        except Exception as exc:
            log.exception("R&D handoff preparation failed")
            if self.rnd_service is not None:self.rnd_service.fail_preparation(cycle,exc)
            else:
                with self.store.connection() as conn:
                    conn.execute(
                        "UPDATE rnd_cycles SET status='FAILED',outcome='FAILED',phase='DECIDING_DIRECTION',"
                        "summary=?,updated_at=CURRENT_TIMESTAMP WHERE cycle_id=? AND status='CREATED'",
                        (f"研发输入或交接准备失败：{exc}",cycle.cycle_id),
                    )
            self.event_bus.publish("RND_STATUS",{"cycle_id":cycle.cycle_id,"status":"FAILED","code":"PREPARATION_FAILED","error":str(exc)})
            return True
        if self.rnd_service is not None:self._start_rnd_task(cycle)
        self.event_bus.publish("RND_CYCLE_CREATED",{"cycle_id":cycle.cycle_id,"game_day":day,"budget":cycle.token_budget,"status":cycle.status,"artifact_dir":str(cycle.artifact_dir)})
        return True

    def _start_rnd_task(self,cycle:Any)->asyncio.Task[Any]|None:
        if self.rnd_service is None:return None
        for task,owned in self._rnd_task_cycles.items():
            if not task.done() and owned.cycle_id==cycle.cycle_id:return task
        task=asyncio.create_task(self.rnd_service.run(cycle),name=f"rnd-{cycle.cycle_id}")
        self._rnd_tasks.add(task);self._rnd_task_cycles[task]=cycle;task.add_done_callback(self._rnd_task_done)
        return task

    def _rnd_task_done(self,task:asyncio.Task[Any])->None:
        cycle=self._rnd_task_cycles.pop(task,None);self._rnd_tasks.discard(task)
        self._next_rnd_retry_at=0.0
        if task.cancelled():return
        try:exc=task.exception()
        except asyncio.CancelledError:return
        if exc is not None:
            log.error("R&D task ended with an unhandled exception",exc_info=(type(exc),exc,exc.__traceback__))
            if cycle is not None and self.rnd_service is not None:self.rnd_service.finalize_unhandled_task(cycle,exc)

    async def _cancel_rnd_tasks(self,reason:str)->None:
        owned=[(task,self._rnd_task_cycles.get(task)) for task in list(self._rnd_tasks) if not task.done()]
        for task,_cycle in owned:task.cancel()
        if owned:
            results=await asyncio.gather(*(task for task,_cycle in owned),return_exceptions=True)
            if self.rnd_service is not None:
                for (_task,cycle),result in zip(owned,results):
                    if cycle is not None and isinstance(result,asyncio.CancelledError):
                        self.rnd_service.finalize_cancelled(cycle,reason)
        self._rnd_tasks.difference_update(task for task,_cycle in owned)
        for task,_cycle in owned:self._rnd_task_cycles.pop(task,None)

    def submit_user_goal(self,objective:str,priority:int=70,success_conditions:list[dict[str,Any]]|None=None,steps:list[dict[str,Any]]|None=None)->dict[str,Any]:
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:raise RuntimeError("当前没有 Minecraft 状态快照")
        conditions=[Condition.model_validate(c) for c in (success_conditions or [{"type":"CUSTOM","args":{"predicate":"action_succeeded"},"description":"至少完成一个真实动作"}])]
        plan_steps=[PlanStep.model_validate(step) for step in (steps or [{"description":"观察环境并为用户目标收集真实信息","tool":"inspect_area","args":{"radius":32}}])]
        for step in plan_steps:CATALOG.validate(step.tool,step.args,allow_templates=True)
        goal=Goal(type=GoalType.CUSTOM,objective=objective.strip(),priority=max(0,min(100,int(priority))),success_conditions=conditions,created_game_day=snapshot.day,source="user")
        plan=Plan(goal_id=goal.goal_id,steps=plan_steps,created_game_day=snapshot.day)
        self.goal_manager.set(goal);self.active_plan=plan;self._save_plan(plan);self._strategic_review_requested=False
        self.event_bus.publish("USER_GOAL_CREATED",{"goal_id":str(goal.goal_id),"plan_id":str(plan.plan_id),"objective":goal.objective})
        return {"goal_id":str(goal.goal_id),"plan_id":str(plan.plan_id)}

    def update_pending_plan_step(self,step_id:str,args:dict[str,Any],reason:str="世界状态变化",replace:bool=False)->dict[str,Any]:
        if self.active_plan is None:raise RuntimeError("当前没有可调整的临时计划")
        update=PlanUpdate(step_id=step_id,args=args,reason=reason,replace=replace)
        applied=self.planner.apply_updates(self.active_plan,[update])
        if not applied:raise ValueError("只能调整尚未执行的步骤，并且参数必须符合动作要求")
        self._save_plan(self.active_plan)
        self.event_bus.publish("PLAN_UPDATED",{"plan_id":str(self.active_plan.plan_id),"step_ids":applied,"revision":self.active_plan.revision})
        return {"plan_id":str(self.active_plan.plan_id),"step_id":step_id,"revision":self.active_plan.revision}

    def queue_blueprint(self, blueprint:dict[str,Any], origin:dict[str,Any], rotation:int=0, priority:int=75)->dict[str,Any]:
        snapshot=self.gateway.latest_snapshot
        if snapshot is None:raise RuntimeError("当前没有 Minecraft 状态快照")
        # Pydantic validation occurs again inside BlueprintExecutor; validate the
        # coordinates now so an invalid GUI request never enters the persisted plan.
        if not isinstance(blueprint,dict) or not isinstance(origin,dict) or not all(k in origin for k in ("x","y","z")):
            raise ValueError("蓝图和 origin.x/y/z 必须完整")
        normalized=Blueprint.model_validate(blueprint).model_dump(mode="json")
        if rotation not in {0,90,180,270}:raise ValueError("rotation 必须为 0/90/180/270")
        if any(isinstance(origin[key],bool) or not isinstance(origin[key],(int,float)) for key in ("x","y","z")):raise ValueError("origin.x/y/z 必须是数字")
        step=PlanStep(description="按蓝图分段施工并验证每个方块",tool="build_blueprint",args={"blueprint":normalized,"origin":origin,"rotation":rotation},timeout_ticks=72_000,max_retries=1)
        goal=Goal(type=GoalType.BUILD,objective=f"建造蓝图：{normalized.get('name') or normalized.get('blueprint_id') or '未命名'}",priority=max(0,min(100,int(priority))),success_conditions=[Condition(type="CUSTOM",args={"predicate":"build_complete"})],created_game_day=snapshot.day,source="user",metadata={"blueprint_id":normalized.get("blueprint_id")})
        plan=Plan(goal_id=goal.goal_id,steps=[step],created_game_day=snapshot.day)
        self.goal_manager.set(goal);self.active_plan=plan;self._save_plan(plan);self._strategic_review_requested=False
        self.event_bus.publish("BLUEPRINT_QUEUED",{"goal_id":str(goal.goal_id),"plan_id":str(plan.plan_id),"blueprint_id":normalized.get("blueprint_id")})
        return {"goal_id":str(goal.goal_id),"plan_id":str(plan.plan_id)}

    def queue_building_dsl(self,dsl:dict[str,Any],origin:dict[str,Any],rotation:int=0,priority:int=75)->dict[str,Any]:
        blueprint=compile_dsl(dsl)
        return self.queue_blueprint(blueprint.model_dump(mode="json"),origin,rotation,priority)

    def request_strategic_review(self)->None:
        self._strategic_review_requested=True;self._last_review=0

    def status_snapshot(self)->dict[str,Any]:
        snapshot=self.gateway.latest_snapshot;goal=self.goal_manager.current;plan=self.active_plan
        return {"mode":self.mode,"desired_running":self._desired_running,"bridge_connected":self.gateway.connected,"bridge_hello":self.gateway.hello,"bound_maid_uuid":self.gateway.bound_maid_uuid,
            "game_day":snapshot.day if snapshot else None,"health":snapshot.health if snapshot else None,"max_health":snapshot.max_health if snapshot else None,"hunger":snapshot.hunger if snapshot else None,
            "nearby_threats":[e.model_dump(mode="json") for e in (snapshot.nearby_entities if snapshot else []) if e.category in {"HOSTILE","MONSTER","ENEMY"}][:20],
            "strategy":self.strategy.model_dump(mode="json"),"goal":goal.model_dump(mode="json") if goal else None,"plan":plan.model_dump(mode="json") if plan else None,"current_action":self.current_action,
            "provider_enabled":self.provider is not None,"rnd_active":sum(1 for t in self._rnd_tasks if not t.done()),"memory":self.store.summary(),"threat":self.threats.context_summary(snapshot.day if snapshot else 0),
            "snapshot":snapshot.model_dump(mode="json") if snapshot else None,"last_error":self.last_error,"recovery_waiting_for_resync":self._recovery_waiting,"reflections":self.reflections.recent(10)}
