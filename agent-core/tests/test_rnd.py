import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from maid_agent.brain.strategy import StrategyState
from maid_agent.brain.autonomous_loop import RuntimeController
from maid_agent.control.events import EventBus
from maid_agent.memory.store import MemoryStore
from maid_agent.metrics.scoreboard import Scoreboard
from maid_agent.rnd.handoff import HandoffBuilder
from maid_agent.rnd.harness import HarnessResult, RndHarness
from maid_agent.rnd.locks import exclusive_file_lock
from maid_agent.rnd.models import RndCheckpointReview, RndMode, RndProposal, default_planning_decision
from maid_agent.rnd.service import RndService
from maid_agent.rnd.trigger import RndCycleConflict, RndTrigger
from maid_agent.skills.store import SkillStore
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


def test_every_fifth_day_is_idempotent_and_builds_handoff(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, budget=100_000_000)
    assert trigger.create_if_due(4) is None
    cycle = trigger.create_if_due(5)
    assert cycle is not None
    assert cycle.cycle_id == "cycle-001"
    assert trigger.create_if_due(5) is None

    builder = HandoffBuilder(store, SkillStore(store), Scoreboard(), tmp_path)
    strategy = StrategyState(
        long_term_objective="持续生存和发展",
        mid_term_objectives=[],
        current_focus="观察",
        known_constraints=[],
        open_problems=[],
        decision_summary="测试",
        last_review_game_day=5,
    )
    input_dir = builder.prepare_input(cycle, strategy, ["action.move_to"])
    root = builder.create_default_output(cycle)
    assert (input_dir / "period_summary.json").exists()
    assert (root / "handoff_manifest.json").exists()
    assert builder.validate_manifest(root / "handoff_manifest.json") == []


def test_cycle_creation_is_atomic_and_never_reuses_an_active_cycle(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000_000)
    second_trigger = RndTrigger(MemoryStore(tmp_path / "state.sqlite3"), tmp_path / "handoff", cycle_days=5, token_budget=100_000_000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(candidate.create_if_due, 5) for candidate in (trigger, second_trigger)]
        results = [future.result() for future in futures]
    assert sum(result is not None for result in results) == 1
    assert len(list((tmp_path / "handoff").glob("cycle-*"))) == 1
    with pytest.raises(RndCycleConflict):
        trigger.create(5)


@pytest.mark.asyncio
async def test_cycle_run_lock_rejects_a_second_service_start(tmp_path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowOrchestrator:
        provider = object()
        async def plan_cycle(self, cycle):
            started.set()
            await release.wait()
            return default_planning_decision().fit_cycle_budget(cycle.token_budget)
        async def propose(self, _cycle, planning):
            return RndProposal(summary="bounded", planning=planning)

    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        def readiness(self): return RndMode.FULL_HARNESS, []
        async def run(self, cycle, *, attempt=1):
            return HarnessResult(True, RndMode.FULL_HARNESS, "SUCCESS", "ok", tmp_path, cycle.artifact_dir / "output", {})

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    service = RndService(store, SkillStore(store), EventBus(), Harness(), SlowOrchestrator())
    first = asyncio.create_task(service.run(cycle))
    await started.wait()
    duplicate = await service.run(cycle)
    assert duplicate["outcome"] == "ALREADY_RUNNING"
    release.set()
    assert (await first)["outcome"] == "COMPLETED"


@pytest.mark.asyncio
async def test_checkpoints_run_production_logic_once_and_force_terminal(tmp_path) -> None:
    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        def readiness(self): return RndMode.FULL_HARNESS, []

    class Orchestrator:
        provider = object()
        review_calls = 0
        async def reassess(self, _cycle, proposal, checkpoint):
            self.review_calls += 1
            threshold = int(checkpoint["active_threshold"])
            return RndCheckpointReview(
                decision="SHRINK" if threshold == 50 else "FINISH",
                direction_still_valuable=True,
                remaining_budget_can_form_result=True,
                revised_scope=proposal.planning.current_cycle_scope,
                reason="bounded review",
            )

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    orchestrator = Orchestrator()
    service = RndService(store, SkillStore(store), EventBus(), Harness(), orchestrator)
    assert service._claim_cycle(cycle)
    proposal = RndProposal(summary="result", planning=default_planning_decision().fit_cycle_budget(1000))
    ledger = TokenLedger(store)

    def spend(request_id: str, total: int) -> None:
        ledger.record(ledger="rnd", purpose="test", model="mock", request_id=request_id,
                      usage=TokenUsage(total, 0, total), cycle_id=cycle.cycle_id)

    spend("c50", 500)
    checkpoint, reviews, stop = await service._apply_due_checkpoints(cycle, proposal, [])
    assert stop is None and checkpoint["triggered_checkpoints"] == [50] and orchestrator.review_calls == 1
    checkpoint, reviews, stop = await service._apply_due_checkpoints(cycle, proposal, reviews)
    assert stop is None and orchestrator.review_calls == 1
    spend("c75", 250)
    checkpoint, reviews, stop = await service._apply_due_checkpoints(cycle, proposal, reviews)
    assert stop is None and checkpoint["triggered_checkpoints"] == [50, 75] and orchestrator.review_calls == 2
    spend("c85", 100)
    checkpoint, reviews, stop = await service._apply_due_checkpoints(cycle, proposal, reviews)
    assert stop is None and checkpoint["allowed_work"] == ["complete", "repair", "compile", "verify", "organize"]
    spend("c95", 100)
    checkpoint, reviews, stop = await service._apply_due_checkpoints(cycle, proposal, reviews)
    assert stop and checkpoint["triggered_checkpoints"] == [50, 75, 85, 95]


@pytest.mark.asyncio
async def test_runtime_cancel_awaits_rnd_and_closes_database_state(tmp_path) -> None:
    started = asyncio.Event()

    class SlowOrchestrator:
        provider = object()
        async def plan_cycle(self, _cycle):
            started.set()
            await asyncio.Event().wait()

    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        def readiness(self): return RndMode.FULL_HARNESS, []

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    service = RndService(store, SkillStore(store), EventBus(), Harness(), SlowOrchestrator())
    runtime = object.__new__(RuntimeController)
    runtime.rnd_service = service
    runtime._rnd_tasks = set()
    runtime._rnd_task_cycles = {}
    runtime._start_rnd_task(cycle)
    await asyncio.wait_for(started.wait(), timeout=2)
    await runtime._cancel_rnd_tasks("test cancel")

    with store.connection() as conn:
        row = conn.execute("SELECT status,owner_pid FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
    assert row["status"] == "SUSPENDED" and row["owner_pid"] is None
    assert runtime._rnd_tasks == set() and runtime._rnd_task_cycles == {}
    with exclusive_file_lock(cycle.artifact_dir.parent / ".rnd-cycle.lock") as acquired:
        assert acquired is True
    result = service._read_json(cycle.artifact_dir / "output" / "rnd_result.json")
    assert result["code"] == "SUSPENDED" and result["budget"]["used"] == 0


@pytest.mark.asyncio
async def test_normal_shutdown_resumes_same_cycle_without_replanning_or_rebuilding(tmp_path) -> None:
    class Orchestrator:
        provider = object()
        plan_calls = 0
        proposal_calls = 0
        async def plan_cycle(self, _cycle):
            self.plan_calls += 1
            raise AssertionError("persisted direction must be reused")
        async def propose(self, _cycle, _planning):
            self.proposal_calls += 1
            raise AssertionError("persisted proposal must be reused")

    class Harness:
        source_workspace = tmp_path / "source"
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        run_calls = 0
        resume_calls = 0
        def readiness(self): return RndMode.FULL_HARNESS, []
        async def run(self, _cycle, *, attempt=1):
            self.run_calls += 1
            raise AssertionError("a suspended harness workspace must not be rebuilt")
        async def resume(self, cycle, *, attempt=1):
            self.resume_calls += 1
            workspace = self.work_root / cycle.cycle_id / f"attempt-{attempt:02d}" / "source"
            assert (workspace / "kept.txt").read_text(encoding="utf-8") == "keep"
            output = cycle.artifact_dir / "output" / f"harness-attempt-{attempt:02d}"
            return HarnessResult(True, RndMode.FULL_HARNESS, "SUCCESS", "resumed", workspace, output, {})

    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000)
    cycle = trigger.create(5)
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "period_summary.json").write_text("{}", encoding="utf-8")
    (input_dir / "runtime_evidence.json").write_text("{}", encoding="utf-8")
    output = cycle.artifact_dir / "output"
    attempt_output = output / "harness-attempt-01"
    attempt_output.mkdir(parents=True)
    planning = default_planning_decision().fit_cycle_budget(1000)
    proposal = RndProposal(summary="persisted proposal", planning=planning)
    (output / "rnd_budget_plan.json").write_text(planning.model_dump_json(), encoding="utf-8")
    (output / "rnd_proposal.json").write_text(proposal.model_dump_json(), encoding="utf-8")
    workspace = tmp_path / "worktrees" / cycle.cycle_id / "attempt-01" / "source"
    workspace.mkdir(parents=True)
    (workspace / "kept.txt").write_text("keep", encoding="utf-8")
    (attempt_output / "harness_result.json").write_text(json.dumps({
        "ok": False, "mode": "FULL_HARNESS", "code": "CANCELLED", "summary": "cancelled",
        "workspace": str(workspace), "details": {"attempt": 1},
    }), encoding="utf-8")
    TokenLedger(store).record(
        ledger="rnd", purpose="before_shutdown", model="mock", request_id="used-before-shutdown",
        usage=TokenUsage(120, 0, 120), cycle_id=cycle.cycle_id,
    )
    with store.connection() as conn:
        conn.execute(
            "UPDATE rnd_cycles SET status='RUNNING',phase='TESTING',owner_pid=? WHERE cycle_id=?",
            (os.getpid(), cycle.cycle_id),
        )
    harness = Harness()
    service = RndService(store, SkillStore(store), EventBus(), harness, Orchestrator())
    suspended = service.finalize_cancelled(cycle, "normal shutdown", proposal)
    assert suspended["outcome"] == "SUSPENDED" and suspended["budget"]["used"] == 120
    with store.connection() as conn:
        row = conn.execute("SELECT status,outcome FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
    assert row["status"] == "SUSPENDED" and row["outcome"] is None

    restarted_store = MemoryStore(tmp_path / "state.sqlite3")
    restarted_orchestrator = Orchestrator()
    restarted_service = RndService(
        restarted_store, SkillStore(restarted_store), EventBus(), harness, restarted_orchestrator,
    )
    recovered = restarted_service.recover_interrupted_cycles()
    assert [item.cycle_id for item in recovered] == ["cycle-001"]
    result = await restarted_service.run(recovered[0])
    assert result["outcome"] == "COMPLETED"
    assert harness.resume_calls == 1 and harness.run_calls == 0
    assert restarted_orchestrator.plan_calls == 0 and restarted_orchestrator.proposal_calls == 0
    assert (workspace / "kept.txt").exists()
    with restarted_store.connection() as conn:
        used = conn.execute(
            "SELECT SUM(total_tokens) FROM token_usage WHERE cycle_id=?", (cycle.cycle_id,),
        ).fetchone()[0]
    assert used == 120
    next_cycle = RndTrigger(
        restarted_store, tmp_path / "handoff", cycle_days=5, token_budget=1000,
    ).create_if_due(10)
    assert next_cycle is not None and next_cycle.cycle_id == "cycle-002"


def test_waiting_user_is_not_automatically_resumed(tmp_path) -> None:
    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        def readiness(self): return RndMode.FULL_HARNESS, []

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    output = cycle.artifact_dir / "output"
    output.mkdir()
    (output / "rnd_result.json").write_text(json.dumps({
        "outcome": "WAITING_USER", "code": "USER_INSTALL_REQUIRED", "reason": "install required",
    }), encoding="utf-8")
    with store.connection() as conn:
        conn.execute(
            "UPDATE rnd_cycles SET status='WAITING_USER',outcome='WAITING_USER',owner_pid=NULL WHERE cycle_id=?",
            (cycle.cycle_id,),
        )
    service = RndService(store, SkillStore(store), EventBus(), Harness())
    assert service.recover_interrupted_cycles() == []
    with store.connection() as conn:
        row = conn.execute("SELECT status,outcome FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
    assert row["status"] == "WAITING_USER" and row["outcome"] == "WAITING_USER"


def test_legacy_shutdown_waiting_user_is_recovered_as_the_same_cycle(tmp_path) -> None:
    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        def readiness(self): return RndMode.FULL_HARNESS, []

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir()
    (input_dir / "period_summary.json").write_text("{}", encoding="utf-8")
    (input_dir / "runtime_evidence.json").write_text("{}", encoding="utf-8")
    output = cycle.artifact_dir / "output"
    output.mkdir()
    (output / "rnd_result.json").write_text(json.dumps({
        "outcome": "WAITING_USER", "code": "CANCELLED", "reason": "old normal shutdown",
    }), encoding="utf-8")
    with store.connection() as conn:
        conn.execute(
            "UPDATE rnd_cycles SET status='WAITING_USER',outcome='WAITING_USER' WHERE cycle_id=?",
            (cycle.cycle_id,),
        )
    service = RndService(store, SkillStore(store), EventBus(), Harness())
    recovered = service.recover_interrupted_cycles()
    assert [item.cycle_id for item in recovered] == [cycle.cycle_id]
    with store.connection() as conn:
        row = conn.execute("SELECT status,outcome FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
    assert row["status"] == "CREATED" and row["outcome"] is None


def test_cycle_ids_and_coverage_stay_continuous_when_interval_changes(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")

    def create_and_finish(day: int, interval: int):
        cycle = RndTrigger(
            store, tmp_path / "handoff", cycle_days=interval, token_budget=1000,
        ).create_if_due(day)
        assert cycle is not None
        with store.connection() as conn:
            conn.execute(
                "UPDATE rnd_cycles SET status='COMPLETED',outcome='COMPLETED' WHERE cycle_id=?",
                (cycle.cycle_id,),
            )
        return cycle

    cycles = [
        create_and_finish(5, 5),
        create_and_finish(10, 10),
        create_and_finish(12, 3),
        create_and_finish(14, 7),
    ]
    assert [cycle.cycle_id for cycle in cycles] == ["cycle-001", "cycle-002", "cycle-003", "cycle-004"]
    assert [(cycle.period_start_day, cycle.period_end_day) for cycle in cycles] == [
        (0, 4), (5, 9), (10, 11), (12, 13),
    ]


def test_cycle_id_allocation_continues_existing_numeric_history(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000)
    first = trigger.create(5)
    with store.connection() as conn:
        conn.execute("UPDATE rnd_cycles SET status='COMPLETED',outcome='COMPLETED' WHERE cycle_id=?", (first.cycle_id,))
        conn.execute(
            "INSERT INTO rnd_cycles(cycle_id,trigger_day,runtime_period_start_day,runtime_period_end_day,"
            "token_budget,status,mode,artifact_dir,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
            ("cycle-007", 10, 5, 9, 1000, "COMPLETED", "READY", str(tmp_path / "old-cycle-007"), "COMPLETED"),
        )
    cycle = trigger.create_if_due(15)
    assert cycle is not None and cycle.cycle_id == "cycle-008"
    assert (cycle.period_start_day, cycle.period_end_day) == (10, 14)


@pytest.mark.asyncio
async def test_real_harness_resume_keeps_existing_workspace(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "fresh.txt").write_text("fresh", encoding="utf-8")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import argparse, pathlib\n"
        "p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--source'); "
        "p.add_argument('--output'); p.add_argument('--cycle-id'); a=p.parse_args()\n"
        "raise SystemExit(0 if (pathlib.Path(a.source)/'kept.txt').is_file() else 9)\n",
        encoding="utf-8",
    )
    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir()
    work_root = tmp_path / "worktrees"
    workspace = work_root / cycle.cycle_id / "attempt-01" / "source"
    workspace.mkdir(parents=True)
    (workspace / "kept.txt").write_text("keep", encoding="utf-8")
    harness = RndHarness(runner_path=str(runner), source_workspace=source, work_root=work_root)
    result = await harness.resume(cycle, attempt=1)
    assert result.ok is True and result.code == "SUCCESS"
    assert (workspace / "kept.txt").is_file() and not (workspace / "fresh.txt").exists()


@pytest.mark.asyncio
async def test_stale_running_cycle_recovers_then_day_ten_can_start(tmp_path) -> None:
    class Orchestrator:
        provider = object()
        plan_calls = 0
        async def plan_cycle(self, _cycle):
            self.plan_calls += 1
            return default_planning_decision().fit_cycle_budget(1000)
        async def propose(self, _cycle, planning):
            return RndProposal(summary="recovered", planning=planning)

    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        def readiness(self): return RndMode.FULL_HARNESS, []
        async def run(self, cycle, *, attempt=1):
            output = cycle.artifact_dir / "output" / f"harness-attempt-{attempt:02d}"
            return HarnessResult(True, RndMode.FULL_HARNESS, "SUCCESS", "ok", tmp_path, output, {})

    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000)
    cycle = trigger.create(5)
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "period_summary.json").write_text("{}", encoding="utf-8")
    (input_dir / "runtime_evidence.json").write_text("{}", encoding="utf-8")
    output_dir = cycle.artifact_dir / "output"
    output_dir.mkdir()
    planning = default_planning_decision().fit_cycle_budget(1000)
    (output_dir / "rnd_budget_plan.json").write_text(planning.model_dump_json(), encoding="utf-8")
    with store.connection() as conn:
        conn.execute(
            "UPDATE rnd_cycles SET status='RUNNING',phase='DESIGNING',owner_pid=999999 WHERE cycle_id=?",
            (cycle.cycle_id,),
        )

    orchestrator = Orchestrator()
    service = RndService(store, SkillStore(store), EventBus(), Harness(), orchestrator)
    recovered = service.recover_interrupted_cycles()
    assert [item.cycle_id for item in recovered] == [cycle.cycle_id]
    result = await service.run(recovered[0])
    assert result["outcome"] == "COMPLETED" and orchestrator.plan_calls == 0
    next_cycle = trigger.create_if_due(10)
    assert next_cycle is not None and next_cycle.cycle_id == "cycle-002"


def test_prepare_input_failure_never_leaves_created_cycle(tmp_path) -> None:
    class FailingHandoff:
        def prepare_input(self, *_args, **_kwargs): raise RuntimeError("prepare failed")
        def create_default_output(self, *_args, **_kwargs): raise AssertionError("must not continue")

    class Harness:
        source_workspace = tmp_path
        runner_path = "runner"
        work_root = tmp_path / "worktrees"
        def readiness(self): return RndMode.FULL_HARNESS, []

    store = MemoryStore(tmp_path / "state.sqlite3")
    trigger = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000)
    service = RndService(store, SkillStore(store), EventBus(), Harness())
    runtime = object.__new__(RuntimeController)
    runtime.rnd_trigger = trigger
    runtime.handoff_builder = FailingHandoff()
    runtime.strategy = StrategyState()
    runtime.rnd_service = service
    runtime.store = store
    runtime.event_bus = EventBus()
    runtime._rnd_tasks = set()
    runtime._rnd_task_cycles = {}
    runtime._create_rnd_cycle_if_due(5)
    with store.connection() as conn:
        row = conn.execute("SELECT status,outcome FROM rnd_cycles WHERE trigger_day=5").fetchone()
    assert row["status"] == "FAILED" and row["outcome"] == "FAILED"
    assert trigger.create_if_due(10) is not None


def test_bridge_actions_keep_no_fake_success_and_final_tick_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    hand = (root / "maid-ai-bridge/src/main/java/com/maidaibridge/action/impl/HandUseAction.java").read_text(encoding="utf-8")
    outcome = (root / "maid-ai-bridge/src/main/java/com/maidaibridge/action/impl/HandUseOutcome.java").read_text(encoding="utf-8")
    entity = (root / "maid-ai-bridge/src/main/java/com/maidaibridge/action/impl/InteractEntityAction.java").read_text(encoding="utf-8")
    assert "PLAYER_CONTEXT_REQUIRED" in hand and "PLAYER_CONTEXT_REQUIRED" in outcome
    assert "NO_EFFECT" in outcome
    assert 'data.addProperty("verified_by"' in hand
    gate = entity.index("String finalFailure = finalExecutionFailure(context);")
    interact = entity.index("InteractionResult result = target.interact(null, hand);")
    assert gate < interact
    for check in ("TARGET_OUT_OF_RANGE", "TARGET_NOT_VISIBLE", "WRONG_DIMENSION", "TARGET_DEAD"):
        assert check in entity
