from __future__ import annotations

from maid_agent.actions.catalog import CATALOG
from maid_agent.brain.strategy import StrategyState
from maid_agent.goal.models import Goal,GoalStatus,Plan,PlanNodeKind,StepStatus
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import ActionResult,StateSnapshot


class RuntimeRecovery:
    def __init__(self,store:MemoryStore,verifier:PostconditionVerifier):self.store=store;self.verifier=verifier

    def restore_strategy(self)->StrategyState:
        raw=self.store.load_strategy_state();return StrategyState.model_validate(raw) if raw else StrategyState()

    def load_pending(self)->tuple[Goal|None,Plan|None,list[str]]:
        """Load recovery metadata without changing any executable state.

        In particular, a persisted RUNNING step remains RUNNING until a fresh Bridge
        HELLO + state resync has arrived. This phase never clears request ids and never
        writes Goal/Plan rows back to SQLite.
        """
        notes=[]
        goal_raw=self.store.load_latest_active_goal()
        goal=Goal.model_validate(goal_raw) if goal_raw else None
        plan_raw=self.store.load_latest_active_plan(goal_id=str(goal.goal_id) if goal else None)
        plan=Plan.model_validate(plan_raw) if plan_raw else None
        if goal or plan:notes.append("已加载待恢复元数据，等待新的 Bridge 状态后再验证；尚未重放任何动作。")
        return goal,plan,notes

    def revalidate(
        self,
        goal:Goal|None,
        plan:Plan|None,
        snapshot:StateSnapshot,
        *,
        cached_results:dict[str,ActionResult]|None=None,
    )->tuple[Goal|None,Plan|None,list[str]]:
        notes=[];cached_results=cached_results or {}
        if goal:goal.status=GoalStatus.NEEDS_REVALIDATION
        if plan:
            plan.status="NEEDS_REVALIDATION"
            for step in plan.iter_nodes():
                if step.status not in {StepStatus.RUNNING,StepStatus.NEEDS_REVALIDATION}:continue
                if step.kind not in {PlanNodeKind.ACTION,PlanNodeKind.WAIT}:
                    step.status=StepStatus.PAUSED
                    notes.append(f"流程节点等待从检查点继续：{step.description}")
                    continue
                cached=cached_results.get(step.request_id or "")
                if self._confirmed_complete(step,snapshot,cached):
                    step.status=StepStatus.DONE;step.side_effect_verified=True
                    if cached:step.result_data=cached.data
                    notes.append(f"新状态确认步骤已完成：{step.description}")
                    continue
                contract=CATALOG.get(step.tool)
                retry_exhausted=step.retry_count>=step.max_retries
                if retry_exhausted:
                    step.status=StepStatus.BLOCKED;step.last_error_code="RESTART_RETRY_EXHAUSTED"
                    plan.status="BLOCKED";notes.append(f"恢复重试次数已用尽：{step.description}")
                elif contract.side_effect and not self._safe_to_retry(step):
                    step.status=StepStatus.BLOCKED;step.last_error_code="RESTART_UNSAFE_REPLAY"
                    plan.status="BLOCKED";notes.append(f"无法从新世界状态确认副作用，拒绝盲目重放：{step.description}")
                else:
                    step.status=StepStatus.PENDING;step.request_id=None;step.retry_count+=1
                    notes.append(f"新状态未满足后置条件，将使用新 request id 继续：{step.description}")
            if plan.status!="BLOCKED":
                plan.status="PAUSED"
                while plan.current_step_index<len(plan.steps) and plan.steps[plan.current_step_index].status in {StepStatus.DONE,StepStatus.SKIPPED}:
                    plan.current_step_index+=1
            self.store.save_model("plans","plan_id",str(plan.plan_id),plan,status=plan.status)
        if goal:
            if goal.success_conditions and self.verifier.all(goal.success_conditions,snapshot):goal.status=GoalStatus.SUCCESS
            elif plan and plan.status=="BLOCKED":goal.status=GoalStatus.BLOCKED
            else:goal.status=GoalStatus.PAUSED
            self.store.save_model("goals","goal_id",str(goal.goal_id),goal,status=goal.status)
        return goal,plan,notes

    def _confirmed_complete(self,step, snapshot:StateSnapshot, cached:ActionResult|None)->bool:
        if cached and cached.ok and step.success_conditions and self.verifier.all(step.success_conditions,snapshot,cached):return True
        if step.success_conditions and self.verifier.all(step.success_conditions,snapshot):return True
        if step.tool=="place_block":
            try:target=(int(step.args["x"]),int(step.args["y"]),int(step.args["z"]));expected=str(step.args["item_id"])
            except (KeyError,TypeError,ValueError):return False
            return any((int(row.get("x",2**31)),int(row.get("y",2**31)),int(row.get("z",2**31)))==target and str(row.get("id"))==expected for row in snapshot.visible_blocks)
        return False

    @staticmethod
    def _safe_to_retry(step)->bool:
        if not CATALOG.get(step.tool).side_effect:return True
        # These executors are idempotent or fully guarded by current-world checks.
        if step.tool in {"move_to","look_at","face_position","face_entity","stop","follow_entity","move_forward","move_backward","strafe_left","strafe_right","approach_entity","move_away_from_entity","maintain_distance","jump","sneak_on","sneak_off","short_sprint","hold_position","wait","wait_until","equip","select_item","move_item_to_main_hand","move_item_to_off_hand","place_block","open_container","build_blueprint","build_dsl","build_chunk"}:return True
        # The fresh Maid snapshot cannot prove which container slot changed, whether
        # a consumable was activated, or how much damage an attack/region action did.
        # Replaying these after a lost result can repeat a real side effect.
        if step.tool in {"transfer_container","take_from_container","put_into_container","use_block","interact_block","use_item","use_main_hand","use_off_hand","use_item_on_block","interact_entity","attack_entity","dig_region","place_region","cancel_action","run_skill"}:return False
        # Inventory/world-changing actions require a state-only postcondition. Result-
        # only predicates cannot distinguish a completed action after process loss.
        state_conditions={"ITEM_COUNT","TAG_COUNT","HEALTH_AT_LEAST","HUNGER_AT_LEAST","POSITION_WITHIN","NO_HOSTILE_WITHIN","BLOCK_STATE"}
        return bool(step.success_conditions) and all(condition.type in state_conditions for condition in step.success_conditions)
