from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maid_agent.control.events import EventBus
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.development_brief import RndDevelopmentBrief
from maid_agent.rnd.dsh_events import DshRunResult
from maid_agent.rnd.harness import HarnessResult, RndHarness
from maid_agent.rnd.models import (
    RndCheckpointReview, RndOutcome, RndPlanningDecision, RndProjectSize,
)
from maid_agent.rnd.service import RndService
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.skills.store import SkillStore
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


class PassingHarness(RndHarness):
    async def validate_workspace(self, cycle, *, baseline_commit=""):
        workspace = self.workspace_for(cycle)
        output = cycle.artifact_dir / "output" / "final-validator"
        return HarnessResult(
            True, self.readiness()[0], "SUCCESS", "validator passed",
            workspace, output, {"baseline_commit": baseline_commit},
        )


@pytest.mark.asyncio
async def test_service_routes_brief_through_dsh_into_isolated_workspace(tmp_path: Path) -> None:
    source = tmp_path / "production"
    source.mkdir()
    (source / "production.txt").write_text("unchanged\n", encoding="utf-8")

    class Orchestrator:
        provider = object()

        async def plan_cycle(self, _cycle):
            return RndPlanningDecision(
                direction="add one isolated harness marker",
                value_reason="prove the production chain",
                project_size=RndProjectSize.SMALL,
                single_cycle_feasible=True,
                intended_outcome=RndOutcome.COMPLETED,
                current_cycle_scope="write harness-result.txt only in the cycle workspace",
            )

        def prepare_coding_harness(self, cycle, planning, workspace, **_kwargs):
            brief = RndDevelopmentBrief.from_planning(
                cycle_id=cycle.cycle_id,
                token_budget=cycle.token_budget,
                planning=planning,
                evidence={},
            )
            control = workspace / ".maidai-rnd"
            brief.write(control)
            return brief, "write the requested marker inside the current workspace"

    class FakeDshAdapter:
        started = False
        terminated = False
        phases: list[str] = []

        def readiness(self):
            return {
                "available": True, "missing": [], "version": "test-cli",
                "profile_version": "test-profile", "cli_version": "test-cli",
            }

        async def start_cycle(self, *, session_id, workspace, task, phase):
            self.started = True
            assert phase == "RESEARCH"
            assert "current workspace" in task
            return await self._run(session_id=session_id, workspace=workspace, phase=phase)

        async def run_phase(self, *, session_id, workspace, task, phase):
            return await self._run(session_id=session_id, workspace=workspace, phase=phase)

        async def _run(self, *, session_id, workspace, phase):
            self.phases.append(phase)
            if phase == "DEVELOPMENT":
                (workspace / "harness-result.txt").write_text("created by dsh\n", encoding="utf-8")
            return DshRunResult(
                True, "SUCCESS", "completed", "workspace changed",
                session_id, str(workspace), phase, usage={"total_tokens": 12},
            )

        async def suspend(self):
            return None

        async def terminate(self):
            self.terminated = True

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000).create(5)
    harness = PassingHarness(
        runner_path=__file__, source_workspace=source, work_root=tmp_path / "workspaces",
    )
    adapter = FakeDshAdapter()
    service = RndService(
        store, SkillStore(store), EventBus(), harness, Orchestrator(), dsh_adapter=adapter,
    )

    result = await service.run(cycle)

    workspace = harness.workspace_for(cycle)
    assert result["outcome"] == RndOutcome.COMPLETED
    assert adapter.started and adapter.terminated
    assert adapter.phases == ["RESEARCH", "DESIGN", "DEVELOPMENT", "BUILD_FIX", "FINALIZE"]
    assert (workspace / "harness-result.txt").read_text(encoding="utf-8") == "created by dsh\n"
    assert (source / "production.txt").read_text(encoding="utf-8") == "unchanged\n"
    assert not (source / "harness-result.txt").exists()
    assert not (cycle.artifact_dir / "input" / "change_request.json").exists()
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
    assert row["dsh_session_id"] == "maidai-rnd-cycle-001"
    assert Path(row["dsh_workspace"]).resolve() == workspace.resolve()
    assert row["dsh_current_phase"] == "FINALIZE"
    assert row["dsh_last_finish_reason"] == "completed"
    assert row["baseline_commit"]


@pytest.mark.asyncio
async def test_suspended_service_reuses_session_workspace_phase_and_direction(tmp_path: Path) -> None:
    source = tmp_path / "production"
    source.mkdir()
    (source / "base.txt").write_text("production\n", encoding="utf-8")

    class Orchestrator:
        provider = object()

        def __init__(self, *, may_plan: bool) -> None:
            self.may_plan = may_plan
            self.plan_calls = 0

        async def plan_cycle(self, _cycle):
            self.plan_calls += 1
            if not self.may_plan:
                raise AssertionError("resume must reuse the persisted direction")
            return RndPlanningDecision(
                direction="persist this direction",
                value_reason="resume contract",
                project_size=RndProjectSize.SMALL,
                single_cycle_feasible=True,
                intended_outcome=RndOutcome.COMPLETED,
                current_cycle_scope="resume the existing DEVELOPMENT phase",
            )

        def prepare_coding_harness(self, cycle, planning, workspace, **_kwargs):
            brief = RndDevelopmentBrief.from_planning(
                cycle_id=cycle.cycle_id, token_budget=cycle.token_budget,
                planning=planning, evidence={},
            )
            brief.write(workspace / ".maidai-rnd")
            return brief, "continue current workspace"

    started = asyncio.Event()

    class SuspendingAdapter:
        event_handler = None

        def readiness(self):
            return {
                "available": True, "missing": [], "version": "test-cli",
                "profile_version": "test-profile", "cli_version": "test-cli",
            }

        async def start_cycle(self, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        async def suspend(self):
            return DshRunResult(
                False, "SUSPENDED", "suspended", "saved",
                "maidai-rnd-cycle-001", "", "DEVELOPMENT",
            )

        async def terminate(self):
            return None

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000).create(5)
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir()
    (input_dir / "period_summary.json").write_text("{}", encoding="utf-8")
    (input_dir / "runtime_evidence.json").write_text("{}", encoding="utf-8")
    harness = PassingHarness(
        runner_path=__file__, source_workspace=source, work_root=tmp_path / "workspaces",
    )
    first_orchestrator = Orchestrator(may_plan=True)
    first_service = RndService(
        store, SkillStore(store), EventBus(), harness, first_orchestrator,
        dsh_adapter=SuspendingAdapter(),
    )
    task = asyncio.create_task(first_service.run(cycle))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with store.connection() as conn:
        suspended_row = conn.execute(
            "SELECT * FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,),
        ).fetchone()
    persisted_session = suspended_row["dsh_session_id"]
    persisted_workspace = Path(suspended_row["dsh_workspace"])
    persisted_phase = suspended_row["dsh_current_phase"]
    assert suspended_row["status"] == "SUSPENDED"

    class ResumingAdapter(SuspendingAdapter):
        def __init__(self) -> None:
            self.resumed: dict[str, object] | None = None

        async def resume(self, **kwargs):
            if self.resumed is None:
                self.resumed = kwargs
            workspace = Path(kwargs["workspace"])
            (workspace / "resumed.txt").write_text("same workspace\n", encoding="utf-8")
            return DshRunResult(
                True, "SUCCESS", "completed", "resumed",
                str(kwargs["session_id"]), str(workspace), str(kwargs["phase"]),
            )

        async def run_phase(self, **kwargs):
            workspace = Path(kwargs["workspace"])
            return DshRunResult(
                True, "SUCCESS", "completed", "continued",
                str(kwargs["session_id"]), str(workspace), str(kwargs["phase"]),
            )

    restarted_store = MemoryStore(tmp_path / "state.sqlite3")
    restarted_orchestrator = Orchestrator(may_plan=False)
    resumed_adapter = ResumingAdapter()
    restarted_service = RndService(
        restarted_store, SkillStore(restarted_store), EventBus(), harness,
        restarted_orchestrator, dsh_adapter=resumed_adapter,
    )
    recovered = restarted_service.recover_interrupted_cycles()
    assert [item.cycle_id for item in recovered] == [cycle.cycle_id]
    result = await restarted_service.run(recovered[0])

    assert result["outcome"] == RndOutcome.COMPLETED
    assert restarted_orchestrator.plan_calls == 0
    assert resumed_adapter.resumed is not None
    assert resumed_adapter.resumed["session_id"] == persisted_session
    assert Path(resumed_adapter.resumed["workspace"]).resolve() == persisted_workspace.resolve()
    assert resumed_adapter.resumed["phase"] == persisted_phase
    assert (persisted_workspace / "resumed.txt").is_file()


@pytest.mark.asyncio
async def test_five_dsh_phases_keep_one_session_and_apply_all_budget_checkpoints(
    tmp_path: Path,
) -> None:
    source = tmp_path / "production"
    source.mkdir()
    (source / "base.py").write_text("VALUE = 1\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=1000).create(5)
    ledger = TokenLedger(store)

    class Orchestrator:
        provider = object()
        reviews: list[int] = []

        async def plan_cycle(self, _cycle):
            return RndPlanningDecision(
                direction="exercise all production phases",
                value_reason="checkpoint integration",
                project_size=RndProjectSize.SMALL,
                single_cycle_feasible=True,
                intended_outcome=RndOutcome.COMPLETED,
                current_cycle_scope="one bounded change",
            )

        def prepare_coding_harness(self, cycle, planning, workspace, **_kwargs):
            brief = RndDevelopmentBrief.from_planning(
                cycle_id=cycle.cycle_id, token_budget=cycle.token_budget,
                planning=planning, evidence={},
            )
            brief.write(workspace / ".maidai-rnd")
            return brief, "use the fixed direction"

        async def reassess(self, _cycle, proposal, checkpoint):
            self.reviews.append(int(checkpoint["active_threshold"]))
            return RndCheckpointReview(
                decision="CONTINUE", direction_still_valuable=True,
                remaining_budget_can_form_result=True,
                revised_scope=proposal.planning.current_cycle_scope,
                reason="test checkpoint",
            )

    class Adapter:
        event_handler = None
        calls: list[dict[str, str]] = []
        spend = {"RESEARCH": 500, "DESIGN": 250, "DEVELOPMENT": 100, "BUILD_FIX": 100}

        def readiness(self):
            return {
                "available": True, "missing": [], "version": "test",
                "profile_version": "test", "cli_version": "test",
            }

        async def start_cycle(self, **kwargs):
            return await self._run(**kwargs)

        async def run_phase(self, **kwargs):
            return await self._run(**kwargs)

        async def _run(self, **kwargs):
            self.calls.append({
                "session_id": str(kwargs["session_id"]),
                "phase": str(kwargs["phase"]), "task": str(kwargs["task"]),
            })
            amount = self.spend.get(str(kwargs["phase"]), 0)
            if amount:
                ledger.record(
                    ledger="rnd", purpose="phase-test", model="mock",
                    request_id=f"phase-{kwargs['phase']}",
                    usage=TokenUsage(amount, 0, amount), cycle_id=cycle.cycle_id,
                )
            return DshRunResult(
                True, "SUCCESS", "completed", f"{kwargs['phase']} complete",
                str(kwargs["session_id"]), str(kwargs["workspace"]), str(kwargs["phase"]),
            )

        async def suspend(self):
            return None

        async def terminate(self):
            return None

    harness = PassingHarness(
        runner_path=__file__, source_workspace=source, work_root=tmp_path / "workspaces",
    )
    orchestrator = Orchestrator()
    adapter = Adapter()
    service = RndService(
        store, SkillStore(store), EventBus(), harness, orchestrator, dsh_adapter=adapter,
    )
    result = await service.run(cycle)

    assert [item["phase"] for item in adapter.calls] == list(
        ("RESEARCH", "DESIGN", "DEVELOPMENT", "BUILD_FIX", "FINALIZE")
    )
    assert len({item["session_id"] for item in adapter.calls}) == 1
    assert orchestrator.reviews == [50, 75]
    assert "不得新增大型范围" in next(
        item["task"] for item in adapter.calls if item["phase"] == "BUILD_FIX"
    )
    assert result["outcome"] == RndOutcome.STAGE_COMPLETED
    assert result["deepseek_harness"]["completed_phases"] == list(
        ("RESEARCH", "DESIGN", "DEVELOPMENT", "BUILD_FIX", "FINALIZE")
    )
    with store.connection() as conn:
        row = conn.execute(
            "SELECT checkpoint_json FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,),
        ).fetchone()
    checkpoint = json.loads(row["checkpoint_json"])
    assert checkpoint["triggered_checkpoints"] == [50, 75, 85, 95]
