from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from maid_agent.actions.catalog import CATALOG, ToolValidationError
from maid_agent.brain.planner import Planner
from maid_agent.brain.strategy import StrategyDecision, StrategyState
from maid_agent.brain.autonomous_loop import RuntimeController, RuntimeMode
from maid_agent.config import RndBudgetSettings, RuntimeBudgetSettings
from maid_agent.control.events import EventBus
from maid_agent.goal.manager import GoalManager
from maid_agent.goal.models import Condition, Goal, GoalStatus, GoalType, Plan, PlanNodeKind, PlanStep, PlanUpdate, StepStatus
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.memory.capability_gaps import CapabilityGap, CapabilityGapDraft
from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import Position, StateSnapshot
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.tokens.budget_guard import BudgetGuard
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


NEW_TOOLS = {
    "inspect_entity", "inspect_nearby_entities", "inspect_block", "inspect_local_space",
    "face_position", "face_entity", "move_forward", "move_backward", "strafe_left",
    "strafe_right", "approach_entity", "move_away_from_entity", "maintain_distance",
    "jump", "sneak_on", "sneak_off", "short_sprint", "interact_entity",
    "use_main_hand", "use_off_hand", "use_item_on_block", "interact_block", "has_item",
    "select_item", "move_item_to_main_hand", "move_item_to_off_hand", "open_container",
    "take_from_container", "put_into_container", "wait", "wait_until",
}


def snapshot(**updates) -> StateSnapshot:
    values = {
        "dimension": "minecraft:overworld", "day": 5, "time_of_day": 1000,
        "game_tick": 100, "position": Position(x=0, y=64, z=0),
        "health": 20, "max_health": 20, "hunger": 20,
    }
    values.update(updates)
    return StateSnapshot(**values)


def test_new_tool_contracts_accept_normal_args_and_reject_unsafe_args() -> None:
    assert NEW_TOOLS <= CATALOG.names(include_runtime=False)
    uid = "11111111-1111-4111-8111-111111111111"
    assert CATALOG.validate("inspect_entity", {"uuid": uid})["uuid"] == uid
    assert CATALOG.validate("wait", {"duration_ticks": 10, "timeout_ticks": 20})["duration_ticks"] == 10
    assert CATALOG.validate("wait_until", {"condition": {"type": "HEALTH_AT_LEAST", "args": {"value": 10}}, "timeout_ticks": 20})["failure_code"] == "CONDITION_TIMEOUT"
    with pytest.raises(ToolValidationError):
        CATALOG.validate("maintain_distance", {"uuid": uid, "min_distance": 8, "max_distance": 3})
    with pytest.raises(ToolValidationError):
        CATALOG.validate("wait", {"duration_ticks": 30, "timeout_ticks": 20})
    with pytest.raises(ToolValidationError):
        CATALOG.validate("wait_until", {"condition": {"type": "CUSTOM", "args": {}}, "timeout_ticks": 20})


def _runtime_for_plan(world: StateSnapshot, calls: list[str]) -> RuntimeController:
    runtime = object.__new__(RuntimeController)
    runtime.mode = RuntimeMode.RUNNING
    runtime.gateway = SimpleNamespace(connected=True, latest_snapshot=world)
    runtime.verifier = PostconditionVerifier()
    runtime.goal_manager = SimpleNamespace(current=None)
    runtime._save_plan = lambda _plan: None

    async def fake_action(_self, _plan, step, _previous):
        calls.append(step.tool)
        step.status = StepStatus.DONE
        step.result_data = {"tool": step.tool}
        return "DONE", step.result_data

    async def no_adjust(_self, _plan):
        return None

    runtime._execute_action_step = MethodType(fake_action, runtime)
    runtime._maybe_adjust_plan = MethodType(no_adjust, runtime)
    return runtime


@pytest.mark.asyncio
async def test_temporary_plan_if_else_three_repeat_and_interrupt() -> None:
    calls: list[str] = []
    world = snapshot()
    runtime = _runtime_for_plan(world, calls)
    if_step = PlanStep(
        kind=PlanNodeKind.IF, description="按生命值选择",
        condition=Condition(type="HEALTH_AT_LEAST", args={"value": 10}),
        then_steps=[PlanStep(description="A", tool="inspect_local_space")],
        else_steps=[PlanStep(description="B", tool="inspect_area")],
    )
    loop = PlanStep(
        kind=PlanNodeKind.REPEAT, description="重复三次", body=[PlanStep(description="C", tool="inspect_local_space")],
        repeat_count=3, max_iterations=3, max_duration_ticks=100,
        exit_condition=Condition(type="HEALTH_BELOW", args={"value": 0}),
    )
    goal = Goal(type=GoalType.CUSTOM, objective="test", success_conditions=[], created_game_day=5)
    plan = Plan(goal_id=goal.goal_id, steps=[if_step, loop], checkpoint={"started_dimension": world.dimension, "started_game_tick": world.game_tick})
    signal, _ = await runtime._execute_nodes(plan, plan.steps, {}, (), top_level=True)
    assert signal == "DONE" and calls == ["inspect_local_space"] * 4
    assert if_step.result_data["branch"] == "then" and loop.result_data["iterations"] == 3

    interrupted_world = world.model_copy(update={"reflex_state": "DANGER"})
    interrupted = _runtime_for_plan(interrupted_world, [])
    stop_step = PlanStep(description="should not run", tool="inspect_area")
    stopped_plan = Plan(goal_id=goal.goal_id, steps=[stop_step], checkpoint={"started_dimension": world.dimension, "started_game_tick": world.game_tick})
    signal, _ = await interrupted._execute_nodes(stopped_plan, stopped_plan.steps, {}, (), top_level=True)
    assert signal == "PREEMPTED" and stop_step.status == StepStatus.PREEMPTED


def test_pending_plan_parameter_update_is_used_and_executed_step_is_protected() -> None:
    goal = Goal(type=GoalType.CUSTOM, objective="update", success_conditions=[], created_game_day=5)
    step = PlanStep(description="move", tool="move_forward", args={"max_distance": 1})
    plan = Plan(goal_id=goal.goal_id, steps=[step])
    assert plan.update_pending_step(PlanUpdate(step_id=step.step_id, args={"max_distance": 4}, reason="world changed"))
    assert step.args["max_distance"] == 4 and plan.revision == 2
    step.status = StepStatus.DONE
    assert not plan.update_pending_step(PlanUpdate(step_id=step.step_id, args={"max_distance": 8}))


@pytest.mark.asyncio
async def test_keep_current_goal_false_replaces_old_temporary_plan_once(tmp_path: Path) -> None:
    world = snapshot()
    store = MemoryStore(tmp_path / "state.sqlite3")
    verifier = PostconditionVerifier()
    manager = GoalManager(store, verifier)
    old_goal = Goal(type=GoalType.CUSTOM, objective="旧目标", success_conditions=[], created_game_day=5)
    manager.set(old_goal)
    completed = PlanStep(description="已经完成的真实动作", tool="inspect_area", status=StepStatus.DONE)
    pending = PlanStep(description="旧计划待执行动作", tool="inspect_area")
    old_plan = Plan(goal_id=old_goal.goal_id, steps=[completed, pending], status="RUNNING")
    decision = StrategyDecision(
        keep_current_goal=False, goal_type=GoalType.CUSTOM, objective="新目标", priority=70,
        success_conditions=[Condition(type="CUSTOM", args={"predicate": "action_succeeded"})],
        steps=[PlanStep(description="执行新计划", tool="inspect_area", args={"radius": 8})],
        decision_summary="世界变化后切换到新目标", evidence=["world_changed"],
    )
    runtime = object.__new__(RuntimeController)
    runtime.provider = object()
    runtime._strategic_review_requested = True
    runtime._last_review = 0.0
    runtime.gateway = SimpleNamespace(snapshot_version=2, latest_snapshot=world)
    runtime.store = store
    runtime.goal_manager = manager
    runtime.planner = Planner()
    runtime.strategy = StrategyState()
    runtime.event_bus = EventBus()
    runtime.active_plan = old_plan
    runtime._record_capability_gap = lambda *_args, **_kwargs: None

    async def decide(_self, _snapshot):
        return decision

    runtime._decide = MethodType(decide, runtime)
    replaced = await runtime._maybe_adjust_plan(old_plan)
    assert replaced is True and runtime._strategic_review_requested is False
    assert old_plan.status == "ABORTED" and completed.status == StepStatus.DONE
    assert pending.status == StepStatus.ABORTED
    assert manager.current is not None and manager.current.objective == "新目标"
    assert old_goal.status == GoalStatus.ABORTED
    assert runtime.active_plan is not old_plan and runtime.active_plan.goal_id == manager.current.goal_id


def test_capability_gap_persists_and_remains_background_input(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    draft = CapabilityGapDraft(
        desired_objective="与普通生物互动", expression_failure_reason="当前实体不支持该互动",
        missing_capability_type="ENTITY_INTERACTION", impact="无法完成当前临时步骤",
    )
    stored = store.record_capability_gap(CapabilityGap.from_draft(draft, game_day=5, game_tick=120))
    assert stored["occurrence_count"] == 1
    assert store.list_capability_gaps(limit=10)[0]["desired_objective"] == draft.desired_objective


def test_five_day_trigger_and_all_rnd_budget_checkpoints(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000)
    assert trigger.create_if_due(4) is None
    cycle = trigger.create_if_due(5)
    assert cycle is not None and cycle.token_budget == 100_000
    ledger = TokenLedger(store)
    settings = RndBudgetSettings(budget_per_cycle=999_999, max_single_request=100_000)
    guard = BudgetGuard(ledger, RuntimeBudgetSettings(), settings)
    expected = ((50_000, "REEVALUATE"), (25_000, "SCOPE_TO_RESULT"), (10_000, "FINISH_ONLY"), (10_000, "FORCE_CLOSE"))
    for index, (amount, name) in enumerate(expected):
        ledger.record(ledger="rnd", purpose="test", model="mock", request_id=f"r{index}", usage=TokenUsage(amount, 0, amount), cycle_id=cycle.cycle_id)
        checkpoint = guard.rnd_checkpoint(cycle_id=cycle.cycle_id)
        assert checkpoint.budget == 100_000 and checkpoint.checkpoint == name
    settings.budget_per_cycle = 5_000_000
    assert guard.rnd_checkpoint(cycle_id=cycle.cycle_id).budget == 100_000
