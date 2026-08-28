from __future__ import annotations

import json
from pathlib import Path

from maid_agent.rnd.models import RndCycle, RndPlanningDecision, RndProjectSize
from maid_agent.rnd.orchestrator import RndOrchestrator


def test_full_harness_inputs_are_brief_based_and_need_no_patch(tmp_path: Path) -> None:
    cycle = RndCycle("cycle-001", 5, 0, 4, 100_000, tmp_path / "handoff" / "cycle-001")
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir(parents=True)
    for name, value in {
        "runtime_evidence.json": {"events": []},
        "strategy_state.json": {"goal": "survive"},
        "skill_scoreboard.json": [],
        "failed_actions.json": [],
        "capability_gaps.json": {"items": []},
        "rnd_project_history.json": {"items": []},
    }.items():
        (input_dir / name).write_text(json.dumps(value), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    planning = RndPlanningDecision(
        direction="完成一个小型源码改进",
        value_reason="直接减少已确认失败",
        project_size=RndProjectSize.SMALL,
        single_cycle_feasible=True,
        current_cycle_scope="只完成该改进和直接测试",
    ).fit_cycle_budget(cycle.token_budget)

    brief, task = RndOrchestrator(None, None).prepare_coding_harness(
        cycle, planning, workspace,
        baseline_state={"baseline_commit": "a" * 40, "baseline_source_hash": "b" * 64},
    )
    control = workspace / ".maidai-rnd"
    assert brief.cycle_id == cycle.cycle_id
    assert (control / "RND_BRIEF.md").is_file()
    assert (control / "cycle.json").is_file()
    assert (control / "constraints.md").is_file()
    assert not (input_dir / "change_request.json").exists()
    assert "不要只输出方案" in task
