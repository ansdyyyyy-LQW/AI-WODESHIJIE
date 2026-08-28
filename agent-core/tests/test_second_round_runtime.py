from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time

from maid_agent.building.dsl import compile_dsl
from maid_agent.capability.graph import CapabilityGraph
from maid_agent.control.events import EventBus
from maid_agent.control.api import ControlApi
from maid_agent.goal.models import Condition, Goal, GoalStatus, GoalType, Plan, PlanStep, StepStatus
from maid_agent.goal.manager import GoalManager
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.memory.store import MemoryStore
from maid_agent.persist.recovery import RuntimeRecovery
from maid_agent.protocol.models import BridgeEvent, InventoryEntry, NearbyEntity, Position, StateSnapshot
from maid_agent.rnd.harness import HarnessResult
from maid_agent.rnd.models import RndCycle, RndMode, RndProposal, default_planning_decision
from maid_agent.rnd.mod_research.service import ModResearchService
from maid_agent.rnd.service import RndService
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.skills.store import SkillStore
from maid_agent.skills.models import SkillSpec, SkillStep
from maid_agent.threat.analytics import ThreatAnalytics


def state(**updates) -> StateSnapshot:
    values = {
        "dimension": "minecraft:overworld",
        "day": 1,
        "time_of_day": 1000,
        "game_tick": 100,
        "position": Position(x=0, y=64, z=0),
        "health": 20,
        "max_health": 20,
        "hunger": 20,
        "inventory": [],
        "visible_blocks": [],
    }
    values.update(updates)
    return StateSnapshot(**values)


def test_capability_chain_distinguishes_items_from_deployed_stations() -> None:
    graph = CapabilityGraph()
    carried = state(inventory=[InventoryEntry(slot=0, id="minecraft:crafting_table", count=1)])
    assert graph.has(carried, "crafting_table_item")
    assert not graph.has(carried, "crafting_station_ready")
    deployed = carried.model_copy(update={"visible_blocks": [{"id": "minecraft:crafting_table", "x": 1, "y": 64, "z": 0}]})
    assert graph.has(deployed, "crafting_station_ready")
    distant = carried.model_copy(update={"visible_blocks": [{"id": "minecraft:crafting_table", "x": 20, "y": 64, "z": 0}]})
    assert not graph.has(distant, "crafting_station_ready")
    assert graph.first_missing(state(), "iron_pickaxe") == "wood"


def test_mining_chain_equips_correct_tool_and_picks_up_real_drop() -> None:
    graph = CapabilityGraph()
    snapshot = state(
        inventory=[InventoryEntry(slot=0, id="minecraft:wooden_pickaxe", count=1)],
        visible_blocks=[{"id": "minecraft:stone", "x": 1, "y": 64, "z": 0}],
        main_hand_item="minecraft:air",
    )
    steps = graph.next_steps(snapshot, "stone")
    assert [step.tool for step in steps] == ["equip", "find_visible_block", "break_block", "pickup_nearby"]
    assert steps[-1].args["item_id"] == "minecraft:cobblestone"
    verifier = PostconditionVerifier()
    assert not verifier.evaluate(graph.condition("wooden_pickaxe"), snapshot)
    assert verifier.evaluate(graph.condition("wooden_pickaxe"), snapshot.model_copy(update={"main_hand_item": "minecraft:wooden_pickaxe"}))


def test_specific_planks_do_not_use_the_wrong_wood_recipe() -> None:
    graph = CapabilityGraph()
    snapshot = state(inventory=[InventoryEntry(slot=0, id="minecraft:oak_log", count=8)])
    steps = graph.acquisition_steps(snapshot, "minecraft:spruce_planks", 4)
    assert steps and steps[0].tool == "move_to"
    assert all(not (step.tool == "craft" and step.args.get("item_id") == "minecraft:spruce_planks") for step in steps)


def test_material_resolver_keeps_acquiring_exact_counts_and_iron_inputs() -> None:
    graph = CapabilityGraph()
    cobble = state(
        inventory=[InventoryEntry(slot=0, id="minecraft:iron_pickaxe", count=1), InventoryEntry(slot=1, id="minecraft:cobblestone", count=8)],
        visible_blocks=[{"id": "minecraft:stone", "x": 1, "y": 64, "z": 0}],
        main_hand_item="minecraft:iron_pickaxe",
    )
    assert graph.has(cobble, "stone_pickaxe")
    assert any(step.tool == "break_block" for step in graph.acquisition_steps(cobble, "minecraft:cobblestone", 20))

    iron = state(
        inventory=[
            InventoryEntry(slot=0, id="minecraft:iron_ingot", count=8),
            InventoryEntry(slot=1, id="minecraft:raw_iron", count=1),
            InventoryEntry(slot=2, id="minecraft:oak_log", count=1),
        ],
        visible_blocks=[{"id": "minecraft:furnace", "x": 2, "y": 64, "z": 0}],
    )
    assert [step.tool for step in graph.acquisition_steps(iron, "minecraft:iron_block", 1)] == ["find_visible_block", "smelt"]


def test_recovery_blocks_ambiguous_side_effect_and_rekeys_safe_retry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    recovery = RuntimeRecovery(store, PostconditionVerifier())
    goal = Goal(type=GoalType.CUSTOM, objective="recover", success_conditions=[], created_game_day=1)
    unsafe = PlanStep(description="container", tool="transfer_container", args={"x": 0, "y": 64, "z": 0, "item_id": "minecraft:stone"}, status=StepStatus.RUNNING, request_id="old")
    plan = Plan(goal_id=goal.goal_id, steps=[unsafe], status="RUNNING")
    _, blocked, _ = recovery.revalidate(goal, plan, state())
    assert blocked is not None and blocked.steps[0].status == StepStatus.BLOCKED
    assert blocked.steps[0].last_error_code == "RESTART_UNSAFE_REPLAY"

    goal2 = Goal(type=GoalType.CUSTOM, objective="craft", success_conditions=[], created_game_day=1)
    safe = PlanStep(description="craft", tool="craft", args={"item_id": "minecraft:stick", "count": 1}, success_conditions=[Condition(type="ITEM_COUNT", args={"item_id": "minecraft:stick", "count": 1})], status=StepStatus.RUNNING, request_id="old")
    plan2 = Plan(goal_id=goal2.goal_id, steps=[safe], status="RUNNING")
    _, retry, _ = recovery.revalidate(goal2, plan2, state())
    assert retry is not None and retry.steps[0].status == StepStatus.PENDING
    assert retry.steps[0].request_id is None and retry.steps[0].retry_count == 1


def test_recovery_preserves_build_parent_material_child_and_resume_plan(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    verifier = PostconditionVerifier()
    parent = Goal(
        type=GoalType.BUILD,
        objective="build",
        success_conditions=[Condition(type="CUSTOM", args={"predicate": "build_complete"})],
        created_game_day=1,
    )
    parent_plan = Plan(goal_id=parent.goal_id, steps=[PlanStep(description="build", tool="build_blueprint", args={"blueprint": {}, "origin": {}})], status="PAUSED", checkpoint={"completed_indices": [0, 1]})
    child = Goal(
        type=GoalType.ACQUIRE_ITEM,
        objective="material",
        created_game_day=1,
        parent_goal_id=parent.goal_id,
        resume_plan_id=parent_plan.plan_id,
        success_conditions=[Condition(type="ITEM_COUNT", args={"item_id": "minecraft:stone", "count": 1})],
    )
    child_plan = Plan(goal_id=child.goal_id, steps=[PlanStep(description="mine", tool="break_block", args={"x": 1, "y": 64, "z": 0})])
    parent.status = GoalStatus.PAUSED_MATERIALS
    child.status = GoalStatus.ACTIVE
    store.save_model("goals", "goal_id", str(parent.goal_id), parent, status=parent.status)
    store.save_model("plans", "plan_id", str(parent_plan.plan_id), parent_plan, status=parent_plan.status)
    store.save_model("goals", "goal_id", str(child.goal_id), child, status=child.status)
    store.save_model("plans", "plan_id", str(child_plan.plan_id), child_plan, status=child_plan.status)

    loaded_goal, loaded_plan, _ = RuntimeRecovery(store, verifier).load_pending()
    assert loaded_goal is not None and loaded_goal.goal_id == child.goal_id
    assert loaded_plan is not None and loaded_plan.goal_id == child.goal_id
    manager = GoalManager(store, verifier)
    manager.restore(loaded_goal)
    manager.current.status = GoalStatus.SUCCESS
    resumed = manager.resume_parent_if_ready()
    assert resumed is not None and resumed.goal_id == parent.goal_id
    restored_plan = Plan.model_validate(store.load_plan(str(child.resume_plan_id)))
    assert restored_plan.checkpoint["completed_indices"] == [0, 1]


def test_threat_event_and_snapshot_do_not_double_count(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    analytics = ThreatAnalytics(store)
    event = BridgeEvent(event_id="e1", event_type="HOSTILE_CONTACT", game_day=1, time_of_day=1000, period="DAY", data={"count": 1, "entity_uuid": "zombie"})
    analytics.ingest_event(event, 1)
    hostile = NearbyEntity(uuid="zombie", type="minecraft:zombie", category="HOSTILE", distance=5, relative={"dx": 1, "dz": 0})
    snapshot = state(nearby_entities=[hostile])
    analytics.ingest_snapshot(snapshot)
    analytics.ingest_snapshot(snapshot.model_copy(update={"game_tick": 120}))
    assert analytics.context_summary(1)["hostile_contacts"] == 1
    restarted = ThreatAnalytics(store)
    restarted.ingest_snapshot(snapshot.model_copy(update={"game_tick": 140}))
    assert restarted.context_summary(1)["hostile_contacts"] == 1


def test_building_dsl_expands_into_the_single_blueprint_model() -> None:
    floor_spec = {"name": "floor", "operations": [{"op": "floor", "x1": 0, "z1": 0, "x2": 1, "z2": 1, "y": 0, "item": "minecraft:stone"}]}
    solid = compile_dsl(floor_spec)
    hollow = compile_dsl({"name": "box", "operations": [{"op": "box", "origin": {"x": 0, "y": 0, "z": 0}, "size": {"x": 3, "y": 3, "z": 3}, "item": "minecraft:cobblestone", "hollow": True}]})
    assert len(solid.blocks) == 4
    assert len(hollow.blocks) == 26
    assert solid.material_bill() == {"minecraft:stone": 4}
    assert compile_dsl(floor_spec).blueprint_id == solid.blueprint_id


async def test_modrinth_required_dependency_must_match_exact_runtime() -> None:
    service = ModResearchService()
    main_required = {"project_id": "a", "id": "a1", "version_number": "1", "game_versions": ["1.20.1"], "loaders": ["forge"], "files": [{"filename": "a.jar", "url": "https://example/a.jar"}], "dependencies": [{"version_id": "bad", "dependency_type": "required"}]}
    main_optional = {**main_required, "id": "a2", "dependencies": [{"version_id": "bad", "dependency_type": "optional"}]}
    incompatible = {"project_id": "b", "id": "bad", "version_number": "2", "game_versions": ["1.20.1"], "loaders": ["fabric"], "files": [{"filename": "b.jar", "url": "https://example/b.jar"}]}

    async def fake_json(_client, path, **_kwargs):
        return [main_required, main_optional] if path == "/project/a/version" else incompatible

    service._json = fake_json  # type: ignore[method-assign]
    versions = await service._compatible_versions(object(), "a")
    assert versions[0]["status"] == "INCOMPATIBLE"
    assert versions[1]["status"] == "COMPATIBLE"


async def test_rnd_runs_one_corrected_patch_attempt(tmp_path: Path) -> None:
    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        calls = 0
        def readiness(self): return RndMode.FULL_HARNESS, []
        async def run(self, cycle, *, attempt=1):
            self.calls += 1
            ok = attempt == 2
            return HarnessResult(ok, RndMode.FULL_HARNESS, "SUCCESS" if ok else "HARNESS_FAILED", "result", tmp_path / f"attempt-{attempt}", cycle.artifact_dir / "output" / f"harness-attempt-{attempt:02d}", {"error_summary": "first compiler error"} if not ok else {})

    class Orchestrator:
        provider = object()
        repairs = 0
        async def plan_cycle(self, cycle): return default_planning_decision().fit_cycle_budget(cycle.token_budget)
        async def propose(self, _cycle, planning): return RndProposal(summary="patch A", planning=planning)
        async def repair(self, _cycle, _previous, _failure):
            self.repairs += 1
            return RndProposal(summary="patch B", planning=_previous.planning)

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    (cycle.artifact_dir / "output").mkdir(parents=True, exist_ok=True)
    harness, orchestrator = Harness(), Orchestrator()
    service = RndService(store, SkillStore(store), EventBus(), harness, orchestrator, SimpleNamespace())
    result = await service.run(cycle)
    assert harness.calls == 2 and orchestrator.repairs == 1
    assert result["harness"]["ok"] is True
    assert len(result["harness"]["attempts"]) == 2


def test_start_gate_uses_bridge_versions_binding_and_recent_probes() -> None:
    runtime_profile = SimpleNamespace(base_url="https://api.example/v1", chat_completions_path="/chat/completions", model="runtime-model")
    rnd_profile = SimpleNamespace(base_url="https://api.example/v1", chat_completions_path="/chat/completions", model="rnd-model")
    api = object.__new__(ControlApi)
    now = int(time.time() * 1000)
    api.settings = SimpleNamespace(
        setup_complete=True,
        owner_uuid="owner",
        runtime_profile=runtime_profile,
        rnd_profile=rnd_profile,
        api_probes={
            "runtime": {"last_probe_ok": True, "last_probe_at": now, "profile_signature": api._profile_signature(runtime_profile)},
            "rnd": {"last_probe_ok": True, "last_probe_at": now, "profile_signature": api._profile_signature(rnd_profile)},
        },
        minecraft_restart_required=False,
    )
    snapshot = state(owner_uuid="owner")
    api.gateway = SimpleNamespace(
        connected=True,
        hello={"minecraft": "1.20.1", "forge": "47.4.23", "tlm": "1.5.3-forge+mc1.20.1", "bridge_version": "0.3.0", "protocol_version": 1},
        bound_maid_uuid="maid",
        latest_snapshot=snapshot,
    )
    rnd_service = SimpleNamespace(readiness=lambda: {"mode": "FULL_HARNESS", "missing": []}, orchestrator=SimpleNamespace(provider=object()))
    api.runtime = SimpleNamespace(provider=object(), rnd_service=rnd_service)
    assert api._start_gate()["ready"] is True
    api.gateway.hello["forge"] = "47.2.0"
    assert api._start_gate()["ready"] is True
    api.gateway.hello["forge"] = "48.0.1"
    assert api._start_gate()["ready"] is False


def test_candidate_activation_updates_the_runtime_spec_after_full_harness(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    skills = SkillStore(store)
    candidate_dir = tmp_path / "cycle" / "output" / "candidate_skills"
    candidate_dir.mkdir(parents=True)
    (candidate_dir.parent / "harness_result.json").write_text(
        '{"ok": true, "mode": "FULL_HARNESS"}', encoding="utf-8"
    )
    source = candidate_dir / "candidate.json"
    source.write_text("{}", encoding="utf-8")
    spec = SkillSpec(
        skill_id="rnd-observe",
        name="R&D observe",
        created_by="rnd",
        status="CANDIDATE",
        steps=[SkillStep(tool="inspect_area", args={"radius": 8})],
        success=[Condition(type="CUSTOM", args={"predicate": "new_observations", "count": 1})],
    )
    skills.put(spec, source_path=str(source))
    assert skills.set_status(spec.skill_id, spec.version, "ACTIVE")
    active = skills.get(spec.skill_id, spec.version, production_only=True)
    assert active is not None and active.status == "ACTIVE"
