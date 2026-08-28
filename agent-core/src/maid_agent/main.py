from __future__ import annotations

import argparse,asyncio,logging,os,secrets,signal,sys
from contextlib import suppress
from pathlib import Path

from maid_agent.brain.autonomous_loop import RuntimeController
from maid_agent.config import AgentSettings
from maid_agent.control.api import ControlApi
from maid_agent.control.events import EventBus
from maid_agent.goal.models import Condition
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.llm.openai_compatible import OpenAICompatibleProvider
from maid_agent.memory.store import MemoryStore
from maid_agent.metrics.scoreboard import Scoreboard
from maid_agent.rnd.handoff import HandoffBuilder
from maid_agent.rnd.harness import RndHarness
from maid_agent.rnd.mod_research.service import ModResearchService
from maid_agent.rnd.orchestrator import RndOrchestrator
from maid_agent.rnd.service import RndService
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.security.secrets import SecretStore
from maid_agent.skills.models import SkillSpec,SkillStep
from maid_agent.skills.store import SkillStore
from maid_agent.threat.analytics import ThreatAnalytics
from maid_agent.tokens.budget_guard import BudgetGuard
from maid_agent.tokens.ledger import TokenLedger
from maid_agent.transport.ws_server import BridgeGateway


def _project_root()->Path|None:
    here=Path(__file__).resolve()
    for parent in here.parents:
        if (parent/"agent-core").exists() and (parent/"maid-ai-bridge").exists():return parent
    env=os.environ.get("MAIDAI_SOURCE_WORKSPACE")
    return Path(env).resolve() if env and Path(env).exists() else None


def _seed_skills(skills:SkillStore)->None:
    builtins=[
        SkillSpec(skill_id="builtin-observe-resource",name="观察并定位资源",description="观察周边并返回真实可见资源。",goal_tags=["acquire","resource","explore","获取","资源"],steps=[SkillStep(tool="inspect_area",args={"radius":32})],success=[Condition(type="CUSTOM",args={"predicate":"new_observations","count":1})]),
        SkillSpec(skill_id="builtin-retreat",name="从已知威胁撤退",description="使用调用者给出的真实 UUID 撤离。",goal_tags=["recover","defend","撤退","危险"],parameters={"hostile_uuid":"uuid"},steps=[SkillStep(tool="retreat_from",args={"uuid":"$params.hostile_uuid","distance":18})],success=[Condition(type="NO_HOSTILE_WITHIN",args={"radius":10})]),
        SkillSpec(skill_id="builtin-eat",name="进食恢复",description="吃背包中的真实食物。",goal_tags=["food","recover","食物","恢复"],steps=[SkillStep(tool="eat",args={})]),
    ]
    for spec in builtins:
        if skills.get(spec.skill_id,spec.version) is None:skills.put(spec)


async def run(settings:AgentSettings)->None:
    settings.data_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=getattr(logging,settings.log_level),format="%(asctime)s %(levelname)s %(name)s: %(message)s",handlers=[logging.FileHandler(settings.data_dir/"logs"/"agent.log",encoding="utf-8"),logging.StreamHandler()])
    store=MemoryStore(settings.database_path);event_bus=EventBus();gateway=BridgeGateway(settings.host,settings.bridge_port,event_bus);skills=SkillStore(store);_seed_skills(skills)
    ledger=TokenLedger(store);guard=BudgetGuard(ledger,settings.runtime_budget,settings.rnd_budget);secret_store=SecretStore(settings.data_dir)
    runtime_provider=None
    if settings.runtime_profile:
        key=secret_store.get(settings.runtime_profile.api_key_secret_id)
        if key:runtime_provider=OpenAICompatibleProvider(settings.runtime_profile,key,ledger,ledger_name="runtime",budget_guard=guard,game_day_getter=lambda:gateway.latest_snapshot.day if gateway.latest_snapshot else None,store=store)
        else:logging.getLogger(__name__).warning("Runtime provider configured but API key secret is unavailable; deterministic fallback remains active")
    rnd_trigger=RndTrigger(store,settings.data_dir/"handoff",cycle_days=settings.rnd_budget.cycle_game_days,token_budget=settings.rnd_budget.budget_per_cycle)
    def current_cycle_id()->str|None:
        with store.connection() as conn:
            row=conn.execute("SELECT cycle_id FROM rnd_cycles WHERE status='RUNNING' LIMIT 1").fetchone()
        return str(row["cycle_id"]) if row else None
    rnd_provider=None
    if settings.rnd_profile:
        key=secret_store.get(settings.rnd_profile.api_key_secret_id)
        if key:rnd_provider=OpenAICompatibleProvider(settings.rnd_profile,key,ledger,ledger_name="rnd",budget_guard=guard,game_day_getter=lambda:gateway.latest_snapshot.day if gateway.latest_snapshot else None,cycle_id_getter=current_cycle_id,store=store)
        else:logging.getLogger(__name__).warning("R&D provider configured but its separate API key secret is unavailable")
    project=_project_root();source=settings.source_workspace or project or (settings.data_dir/"source-workspace")
    runner=settings.harness_runner_path
    if not runner and project and (project/"rnd-runner"/"src"/"maid_rnd_runner"/"main.py").exists():runner=str(project/"rnd-runner"/"src"/"maid_rnd_runner"/"main.py")
    harness=RndHarness(runner_path=runner,source_workspace=source,work_root=settings.harness_work_dir or settings.data_dir/"rnd-worktrees")
    rnd_service=RndService(store,skills,event_bus,harness,RndOrchestrator(rnd_provider,source),ModResearchService())
    verifier=PostconditionVerifier();threats=ThreatAnalytics(store);scoreboard=Scoreboard(store);handoff=HandoffBuilder(store,skills,scoreboard,source)
    runtime=RuntimeController(gateway=gateway,store=store,token_ledger=ledger,skills=skills,event_bus=event_bus,verifier=verifier,provider=runtime_provider,rnd_trigger=rnd_trigger,handoff_builder=handoff,scoreboard=scoreboard,rnd_service=rnd_service,review_seconds=settings.autonomous_review_seconds,threats=threats)
    stop=asyncio.Event()
    control=ControlApi(settings.host,settings.control_port,settings.control_token,runtime,event_bus,store,skills,ledger,gateway,rnd_trigger,settings,shutdown_event=stop)
    await gateway.start();await control.start();await runtime.recover_rnd_cycles()
    loop=asyncio.get_running_loop();auto_task=None
    if settings.auto_start or bool(store.get_runtime_state("desired_running",False)):
        async def start_when_ready():
            while not stop.is_set():
                if control._start_gate()["ready"]:
                    await runtime.start();return
                await asyncio.sleep(1)
        auto_task=asyncio.create_task(start_when_ready(),name="start-gate-waiter")
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:loop.add_signal_handler(sig,stop.set)
        except (NotImplementedError,RuntimeError):pass
    try:await stop.wait()
    finally:
        if auto_task and not auto_task.done():
            auto_task.cancel()
            with suppress(asyncio.CancelledError):await auto_task
        await runtime.stop();await control.close();await gateway.close()


def main()->int:
    parser=argparse.ArgumentParser(description="MaidAI Agent Core")
    parser.add_argument("--config",type=Path);parser.add_argument("--control-token",default=os.environ.get("MAIDAI_CONTROL_TOKEN",""))
    args=parser.parse_args();settings=AgentSettings.load(args.config,control_token=args.control_token)
    try:asyncio.run(run(settings));return 0
    except KeyboardInterrupt:return 130

if __name__=="__main__":raise SystemExit(main())
