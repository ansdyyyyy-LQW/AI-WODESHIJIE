from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


DEFAULT_PHASE_BUDGETS = {
    "direction_selection": 8_000_000,
    "research": 12_000_000,
    "design": 10_000_000,
    "development": 45_000_000,
    "build_and_fix": 15_000_000,
    "finish_and_reserve": 10_000_000,
}


class RndMode(StrEnum):
    READY = "READY"
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    FULL_HARNESS = "FULL_HARNESS"
    DISABLED = "DISABLED"


class RndProjectSize(StrEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    BEYOND_CYCLE = "BEYOND_CYCLE"


class RndOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    FAILED = "FAILED"
    WAITING_USER = "WAITING_USER"


class RndContinuationDecision(StrEnum):
    NEW = "NEW"
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    ABANDON = "ABANDON"
    RESUME = "RESUME"


class RndPhase(StrEnum):
    DECIDING_DIRECTION = "DECIDING_DIRECTION"
    RESEARCHING = "RESEARCHING"
    DESIGNING = "DESIGNING"
    DEVELOPING = "DEVELOPING"
    TESTING = "TESTING"
    FIXING = "FIXING"
    FINALIZING = "FINALIZING"


class RndPlanningDecision(BaseModel):
    project_id: str = Field(default_factory=lambda: "rnd-project-" + uuid4().hex[:16])
    direction: str = Field(min_length=1, max_length=2000)
    value_reason: str = Field(min_length=1, max_length=3000)
    project_size: RndProjectSize
    single_cycle_feasible: bool
    intended_outcome: RndOutcome = RndOutcome.COMPLETED
    current_cycle_scope: str = Field(min_length=1, max_length=3000)
    continuation_decision: RndContinuationDecision = RndContinuationDecision.NEW
    previous_project_id: str | None = None
    continuation_reason: str = ""
    phase_budgets: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_PHASE_BUDGETS))
    finish_reserve_tokens: int = Field(15_000_000, ge=0)

    @model_validator(mode="after")
    def validate_budget_and_scope(self) -> RndPlanningDecision:
        required = set(DEFAULT_PHASE_BUDGETS)
        if set(self.phase_budgets) != required or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.phase_budgets.values()
        ):
            raise ValueError("phase_budgets must contain the six registered non-negative phases")
        if self.continuation_decision in {RndContinuationDecision.CONTINUE, RndContinuationDecision.RESUME}:
            if not self.previous_project_id:
                raise ValueError("continuing a project requires previous_project_id")
        if not self.single_cycle_feasible and self.intended_outcome == RndOutcome.COMPLETED:
            self.intended_outcome = RndOutcome.STAGE_COMPLETED
        if self.project_size == RndProjectSize.BEYOND_CYCLE:
            self.single_cycle_feasible = False
            self.intended_outcome = RndOutcome.STAGE_COMPLETED
        return self

    @property
    def planned_total(self) -> int:
        return sum(self.phase_budgets.values())

    def fit_cycle_budget(self, budget: int) -> RndPlanningDecision:
        """Scale test/small-cycle plans; the formal 100M cycle retains exact defaults."""
        budget = max(1, int(budget))
        if self.planned_total <= budget and self.finish_reserve_tokens <= budget:
            return self
        weights = self.phase_budgets if self.planned_total > 0 else DEFAULT_PHASE_BUDGETS
        total = max(1, sum(weights.values()))
        scaled = {key: int(budget * value / total) for key, value in weights.items()}
        scaled["finish_and_reserve"] += budget - sum(scaled.values())
        reserve = 15_000_000 if budget >= 100_000_000 else max(1, int(budget * .15))
        return self.model_copy(update={"phase_budgets": scaled, "finish_reserve_tokens": reserve})


def default_planning_decision() -> RndPlanningDecision:
    return RndPlanningDecision(
        direction="等待独立研发模型依据本周期资料选择方向",
        value_reason="尚未配置独立研发模型，当前只保存资料和预算边界",
        project_size=RndProjectSize.SMALL,
        single_cycle_feasible=True,
        intended_outcome=RndOutcome.WAITING_USER,
        current_cycle_scope="准备完整输入并等待研发模型可用",
    )


class RndCycle:
    def __init__(
        self,
        cycle_id: str,
        trigger_day: int,
        period_start_day: int,
        period_end_day: int,
        token_budget: int,
        artifact_dir: Path,
        status: str = "CREATED",
        mode: RndMode | str = RndMode.READY,
        *,
        phase: RndPhase | str = RndPhase.DECIDING_DIRECTION,
        outcome: RndOutcome | str | None = None,
        project_id: str = "",
        project_size: RndProjectSize | str | None = None,
        continuation_decision: RndContinuationDecision | str = RndContinuationDecision.NEW,
    ):
        self.cycle_id = cycle_id
        self.trigger_day = trigger_day
        self.period_start_day = period_start_day
        self.period_end_day = period_end_day
        self.token_budget = token_budget
        self.artifact_dir = Path(artifact_dir)
        self.status = status
        self.mode = mode
        self.phase = phase
        self.outcome = outcome
        self.project_id = project_id
        self.project_size = project_size
        self.continuation_decision = continuation_decision

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "trigger_day": self.trigger_day,
            "period_start_day": self.period_start_day,
            "period_end_day": self.period_end_day,
            "token_budget": self.token_budget,
            "artifact_dir": str(self.artifact_dir),
            "status": self.status,
            "mode": self.mode,
            "phase": self.phase,
            "outcome": self.outcome,
            "project_id": self.project_id,
            "project_size": self.project_size,
            "continuation_decision": self.continuation_decision,
        }


class RndProposal(BaseModel):
    summary: str
    planning: RndPlanningDecision = Field(default_factory=default_planning_decision)
    evidence: list[str] = Field(default_factory=list)
    files_to_change: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    candidate_skills: list[dict[str, Any]] = Field(default_factory=list)
    mod_research_queries: list[str] = Field(default_factory=list)
    expected_behavior_changes: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    disproved_routes: list[str] = Field(default_factory=list)
    continuation_point: str = ""


class RndCheckpointReview(BaseModel):
    decision: str = Field(pattern="^(CONTINUE|SHRINK|FINISH|FAIL)$")
    direction_still_valuable: bool
    remaining_budget_can_form_result: bool
    revised_scope: str
    reason: str
