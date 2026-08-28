from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from maid_agent.rnd.harness import RndHarness
from maid_agent.rnd.models import RndCycle


def test_workspace_copy_is_git_baselined_without_touching_source(tmp_path: Path) -> None:
    source = tmp_path / "production"
    source.mkdir()
    protected = source / "protected.py"
    protected.write_text("VALUE = 1\n", encoding="utf-8")
    before = hashlib.sha256(protected.read_bytes()).hexdigest()
    cycle = RndCycle("cycle-001", 5, 0, 4, 100_000, tmp_path / "handoff")
    cycle.artifact_dir.mkdir()
    harness = RndHarness(runner_path=__file__, source_workspace=source, work_root=tmp_path / "workspaces")

    state = harness.prepare_workspace(cycle)
    workspace = Path(state["workspace"])
    assert len(state["baseline_commit"]) == 40
    assert (workspace / ".git").is_dir()
    (workspace / "protected.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before
    assert harness._git(workspace, "diff", "--name-only", state["baseline_commit"]) == "protected.py"


@pytest.mark.asyncio
async def test_agent_core_calls_final_validator_on_existing_workspace(tmp_path: Path) -> None:
    source = tmp_path / "production"
    package = source / "agent-core" / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    artifact = tmp_path / "handoff" / "cycle-002"
    artifact.mkdir(parents=True)
    cycle = RndCycle("cycle-002", 10, 5, 9, 100_000, artifact)
    project = Path(__file__).resolve().parents[2]
    runner = project / "rnd-runner" / "src" / "maid_rnd_runner" / "main.py"
    harness = RndHarness(
        runner_path=str(runner), source_workspace=source,
        work_root=tmp_path / "workspaces",
    )
    state = harness.prepare_workspace(cycle)
    workspace = Path(state["workspace"])
    (workspace / "agent-core" / "src" / "demo" / "__init__.py").write_text(
        "VALUE = 2\n", encoding="utf-8",
    )

    result = await harness.validate_workspace(
        cycle, baseline_commit=str(state["baseline_commit"]),
    )

    assert result.ok is True and result.code == "SUCCESS"
    runner_result = result.details["runner_result"]
    assert runner_result["validator_decision"] == "PASS"
    assert runner_result["changed_files"] == ["agent-core/src/demo/__init__.py"]
