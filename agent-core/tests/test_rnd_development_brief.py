from __future__ import annotations

import json
from pathlib import Path

from maid_agent.rnd.models import (
    RndCycle,
    RndDevelopmentTarget,
    RndPlanningDecision,
    RndProjectSize,
)
from maid_agent.rnd.orchestrator import RndOrchestrator


def test_cycle_builds_complete_development_brief(tmp_path: Path) -> None:
    cycle = RndCycle("cycle-023", 115, 110, 114, 100_000, tmp_path / "cycle-023")
    input_dir = cycle.artifact_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "runtime_evidence.json").write_text(json.dumps({
        "events": [{"type": "ACTION_FAILED", "payload": {"code": "BLOCKED"}}],
        "capability_gaps": {"items": [{"capability": "example"}]},
        "threat_windows": [],
        "tool_names": ["move_to"],
    }), encoding="utf-8")
    planning = RndPlanningDecision(
        direction="改进当前 MaidAI 的一个已确认能力缺口",
        value_reason="最近五日出现可复现失败",
        development_target=RndDevelopmentTarget.MAIDAI_SOURCE,
        project_size=RndProjectSize.SMALL,
        single_cycle_feasible=True,
        current_cycle_scope="只修改直接相关源码并完成针对性测试",
    ).fit_cycle_budget(cycle.token_budget)

    brief = RndOrchestrator(None, None).build_development_brief(cycle, planning, token_used=1200)
    assert brief.cycle_id == "cycle-023"
    assert brief.development_target == RndDevelopmentTarget.MAIDAI_SOURCE
    assert brief.remaining_budget == 98_800
    assert brief.known_failures[0]["type"] == "ACTION_FAILED"
    assert (cycle.artifact_dir / "output" / "rnd_development_brief.json").is_file()
