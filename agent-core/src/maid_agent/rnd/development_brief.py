from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from maid_agent.rnd.models import (
    RndDevelopmentTarget,
    RndOutcome,
    RndPlanningDecision,
    RndProjectSize,
)


class RndDevelopmentBrief(BaseModel):
    cycle_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    value_reason: str = Field(min_length=1)
    project_size: RndProjectSize
    current_cycle_scope: str = Field(min_length=1)
    development_target: RndDevelopmentTarget
    intended_outcome: RndOutcome
    continuation_of_project: bool = False
    previous_project_id: str | None = None
    allowed_areas: list[str] = Field(min_length=1)
    forbidden_areas: list[str] = Field(min_length=1)
    runtime_evidence_summary: dict[str, Any] = Field(default_factory=dict)
    known_failures: list[dict[str, Any]] = Field(default_factory=list)
    capability_gaps: list[dict[str, Any]] = Field(default_factory=list)
    token_budget: int = Field(gt=0)
    token_used: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    phase_budgets: dict[str, int]
    acceptance_requirements: list[str] = Field(min_length=1)
    handoff_requirements: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_budget_and_continuation(self) -> RndDevelopmentBrief:
        if self.token_used + self.remaining_budget != self.token_budget:
            raise ValueError("token_used + remaining_budget must equal token_budget")
        if any(value < 0 for value in self.phase_budgets.values()):
            raise ValueError("phase budgets must be non-negative")
        if self.continuation_of_project and not self.previous_project_id:
            raise ValueError("continuation requires previous_project_id")
        return self

    @classmethod
    def from_planning(
        cls,
        *,
        cycle_id: str,
        token_budget: int,
        planning: RndPlanningDecision,
        evidence: dict[str, Any],
        token_used: int = 0,
    ) -> RndDevelopmentBrief:
        failures = []
        failure_types = {
            "ACTION_FAILED", "ACTION_STUCK", "ACTION_TIMEOUT", "ACTION_PREEMPTED",
            "MAID_DEATH", "GOAL_BLOCKED", "RUNTIME_ERROR",
        }
        for event in evidence.get("events", []):
            if isinstance(event, dict) and str(event.get("type", "")) in failure_types:
                failures.append(event)
                if len(failures) >= 100:
                    break
        gaps_raw = evidence.get("capability_gaps") or {}
        if isinstance(gaps_raw, dict):
            gaps_raw = gaps_raw.get("items") or []
        gaps = [item for item in gaps_raw if isinstance(item, dict)][:200]
        allowed_by_target = {
            RndDevelopmentTarget.MAIDAI_SOURCE: [
                "agent-core/", "maid-ai-bridge/", "control-center/", "rnd-runner/",
                "dsh-integration/", "tools/", "docs/", ".github/",
                "BUILD_ALL_WINDOWS.bat", "PACKAGE_WINDOWS.bat", "README.md",
                "README_CN.txt", "CHANGELOG.md",
            ],
            RndDevelopmentTarget.NEW_FORGE_ADDON: ["rnd-projects/<project-id>/"],
            RndDevelopmentTarget.SKILL: ["agent-core/", ".maidai-rnd/candidates/"],
            RndDevelopmentTarget.EXTERNAL_MOD_RESEARCH: [".maidai-rnd/", "docs/"],
            RndDevelopmentTarget.RESEARCH_ONLY: [".maidai-rnd/"],
        }
        token_used = min(max(0, int(token_used)), int(token_budget))
        continuation = planning.continuation_decision.value in {"CONTINUE", "RESUME"}
        return cls(
            cycle_id=cycle_id,
            project_id=planning.project_id,
            direction=planning.direction,
            value_reason=planning.value_reason,
            project_size=planning.project_size,
            current_cycle_scope=planning.current_cycle_scope,
            development_target=planning.development_target,
            intended_outcome=planning.intended_outcome,
            continuation_of_project=continuation,
            previous_project_id=planning.previous_project_id,
            allowed_areas=allowed_by_target[planning.development_target],
            forbidden_areas=[
                "workspace 外的正式源码", "Minecraft mods 目录", "API Key 与凭据",
                ".git/ 内部文件", "自动安装或重启 Minecraft",
            ],
            runtime_evidence_summary={
                "event_count": len(evidence.get("events", [])),
                "failure_count": len(failures),
                "threat_window_count": len(evidence.get("threat_windows", [])),
                "available_tool_count": len(evidence.get("tool_names", [])),
            },
            known_failures=failures,
            capability_gaps=gaps,
            token_budget=int(token_budget),
            token_used=token_used,
            remaining_budget=int(token_budget) - token_used,
            phase_budgets=dict(planning.phase_budgets),
            acceptance_requirements=[
                "真实修改必须存在于隔离 workspace 文件中，不能只在回复中给方案或补丁。",
                "运行与当前修改直接相关的构建或测试，并根据真实错误修正。",
                "最终结果必须由 maid-rnd Final Validator 独立复验。",
            ],
            handoff_requirements=[
                "保留 git diff、验证结果和可交付产物清单。",
                "新 Mod 只能进入 handoff，不得自动安装到 Minecraft。",
                "不保存 API Key、凭据或私有思维过程。",
            ],
        )

    def to_markdown(self) -> str:
        data = self.model_dump(mode="json")
        return (
            f"# MaidAI R&D Brief — {self.cycle_id}\n\n"
            f"## 研发方向\n\n{self.direction}\n\n"
            f"## 本周期范围\n\n{self.current_cycle_scope}\n\n"
            f"## 价值原因\n\n{self.value_reason}\n\n"
            f"## 研发目标类型\n\n{self.development_target.value}\n\n"
            f"## 允许修改区域\n\n" + "\n".join(f"- {item}" for item in self.allowed_areas) + "\n\n"
            f"## 禁止区域\n\n" + "\n".join(f"- {item}" for item in self.forbidden_areas) + "\n\n"
            f"## Token 预算\n\n"
            f"- 总预算：{self.token_budget}\n- 已使用：{self.token_used}\n- 剩余：{self.remaining_budget}\n\n"
            f"## 验收要求\n\n" + "\n".join(f"- {item}" for item in self.acceptance_requirements) + "\n\n"
            f"## 结构化 Brief\n\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```\n"
        )

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "RND_BRIEF.md").write_text(self.to_markdown(), encoding="utf-8")
        (directory / "brief.json").write_text(self.model_dump_json(indent=2), encoding="utf-8")
