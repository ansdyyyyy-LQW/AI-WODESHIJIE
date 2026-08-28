from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class CapabilityGapDraft(BaseModel):
    desired_objective: str = Field(min_length=1, max_length=1000)
    expression_failure_reason: str = Field(min_length=1, max_length=2000)
    missing_capability_type: str = Field(min_length=1, max_length=300)
    impact: str = Field(min_length=1, max_length=1000)


class CapabilityGap(CapabilityGapDraft):
    gap_id: str
    occurrence_count: int = Field(1, ge=1)
    last_game_day: int = Field(0, ge=0)
    last_game_tick: int = Field(0, ge=0)
    last_occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "OPEN"

    @classmethod
    def from_draft(cls, draft: CapabilityGapDraft, *, game_day: int, game_tick: int) -> "CapabilityGap":
        normalized = "|".join(
            part.strip().lower()
            for part in (draft.desired_objective, draft.missing_capability_type)
        )
        gap_id = "gap_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return cls(
            gap_id=gap_id,
            **draft.model_dump(),
            last_game_day=max(0, game_day),
            last_game_tick=max(0, game_tick),
        )
