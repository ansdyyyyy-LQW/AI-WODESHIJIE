from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class GoalType(StrEnum):
    ACQUIRE_ITEM = "ACQUIRE_ITEM"
    SECURE_FOOD = "SECURE_FOOD"
    IMPROVE_EQUIPMENT = "IMPROVE_EQUIPMENT"
    EXPLORE = "EXPLORE"
    BUILD = "BUILD"
    DEFEND = "DEFEND"
    RECOVER = "RECOVER"
    STORE_ITEMS = "STORE_ITEMS"
    CUSTOM = "CUSTOM"


class GoalStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    PAUSED_MATERIALS = "PAUSED_MATERIALS"
    NEEDS_REVALIDATION = "NEEDS_REVALIDATION"
    ABORTED = "ABORTED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    PAUSED = "PAUSED"
    PREEMPTED = "PREEMPTED"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"
    NEEDS_REVALIDATION = "NEEDS_REVALIDATION"


class PlanNodeKind(StrEnum):
    ACTION = "ACTION"
    WAIT = "WAIT"
    IF = "IF"
    BRANCH = "BRANCH"
    REPEAT = "REPEAT"
    WHILE = "WHILE"
    UNTIL = "UNTIL"
    ABORT = "ABORT"
    PAUSE = "PAUSE"


class Condition(BaseModel):
    type: Literal[
        "ITEM_COUNT", "TAG_COUNT", "HEALTH_AT_LEAST", "HEALTH_BELOW", "HUNGER_AT_LEAST",
        "POSITION_WITHIN", "NO_HOSTILE_WITHIN", "ENTITY_EXISTS", "ENTITY_GONE",
        "ENTITY_DISTANCE_AT_MOST", "ENTITY_DISTANCE_AT_LEAST", "ENTITY_TARGETING_MAID",
        "ACTION_CODE", "ACTION_STATUS", "WORLD_DELTA", "INVENTORY_DELTA", "BLOCK_STATE",
        "BLOCK_AIR", "LOCAL_SPACE", "CUSTOM",
    ]
    args: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


# Flow conditions deliberately exclude CUSTOM. Goal/postcondition compatibility keeps
# CUSTOM available, but temporary control flow never evaluates arbitrary expressions.
FLOW_CONDITION_TYPES = frozenset({
    "ITEM_COUNT", "TAG_COUNT", "HEALTH_AT_LEAST", "HEALTH_BELOW", "HUNGER_AT_LEAST",
    "POSITION_WITHIN", "NO_HOSTILE_WITHIN", "ENTITY_EXISTS", "ENTITY_GONE",
    "ENTITY_DISTANCE_AT_MOST", "ENTITY_DISTANCE_AT_LEAST", "ENTITY_TARGETING_MAID",
    "ACTION_CODE", "ACTION_STATUS", "WORLD_DELTA", "INVENTORY_DELTA", "BLOCK_STATE",
    "BLOCK_AIR", "LOCAL_SPACE",
})


def ensure_flow_condition(condition: Condition | None, label: str) -> None:
    if condition is None:
        raise ValueError(f"{label} requires a condition")
    if condition.type not in FLOW_CONDITION_TYPES:
        raise ValueError(f"{label} uses an unregistered flow condition")


class Goal(BaseModel):
    goal_id: UUID = Field(default_factory=uuid4)
    type: GoalType
    objective: str
    priority: int = Field(50, ge=0, le=100)
    success_conditions: list[Condition]
    failure_conditions: list[Condition] = Field(default_factory=list)
    created_game_day: int
    deadline_game_tick: int | None = None
    status: GoalStatus = GoalStatus.PENDING
    source: Literal["runtime_llm", "fallback", "skill", "recovery", "user", "capability", "building"] = "runtime_llm"
    parent_goal_id: UUID | None = None
    resume_plan_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanBranch(BaseModel):
    condition: Condition
    steps: list[PlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_condition(self) -> PlanBranch:
        ensure_flow_condition(self.condition, "BRANCH")
        if not self.steps:
            raise ValueError("BRANCH requires at least one step")
        return self


class PlanStep(BaseModel):
    step_id: UUID = Field(default_factory=uuid4)
    kind: PlanNodeKind = PlanNodeKind.ACTION
    description: str
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[Condition] = Field(default_factory=list)
    success_conditions: list[Condition] = Field(default_factory=list)
    condition: Condition | None = None
    then_steps: list[PlanStep] = Field(default_factory=list)
    else_steps: list[PlanStep] = Field(default_factory=list)
    body: list[PlanStep] = Field(default_factory=list)
    branches: list[PlanBranch] = Field(default_factory=list)
    repeat_count: int = Field(1, ge=1, le=100)
    max_iterations: int = Field(1, ge=1, le=100)
    max_duration_ticks: int = Field(1200, ge=1, le=72000)
    exit_condition: Condition | None = None
    status: StepStatus = StepStatus.PENDING
    retry_count: int = 0
    max_retries: int = Field(2, ge=0, le=5)
    timeout_ticks: int = Field(1200, ge=1, le=72000)
    last_error_code: str | None = None
    request_id: str | None = None
    result_data: dict[str, Any] = Field(default_factory=dict)
    side_effect_verified: bool = False

    @model_validator(mode="after")
    def validate_flow_shape(self) -> PlanStep:
        if self.kind in {PlanNodeKind.ACTION, PlanNodeKind.WAIT}:
            if not self.tool:
                raise ValueError(f"{self.kind} requires tool")
            if self.kind == PlanNodeKind.WAIT and self.tool not in {"wait", "wait_until"}:
                raise ValueError("WAIT nodes may only call wait or wait_until")
        elif self.kind == PlanNodeKind.IF:
            ensure_flow_condition(self.condition, "IF")
            if not self.then_steps:
                raise ValueError("IF requires then_steps")
        elif self.kind == PlanNodeKind.BRANCH:
            if not self.branches:
                raise ValueError("BRANCH requires branches")
        elif self.kind in {PlanNodeKind.REPEAT, PlanNodeKind.WHILE, PlanNodeKind.UNTIL}:
            if not self.body:
                raise ValueError(f"{self.kind} requires body")
            ensure_flow_condition(self.exit_condition, str(self.kind))
            if self.kind == PlanNodeKind.WHILE:
                ensure_flow_condition(self.condition, "WHILE")
            if self.repeat_count > self.max_iterations:
                raise ValueError("repeat_count cannot exceed max_iterations")
        return self

    def iter_nodes(self):
        yield self
        for child in self.then_steps:
            yield from child.iter_nodes()
        for child in self.else_steps:
            yield from child.iter_nodes()
        for child in self.body:
            yield from child.iter_nodes()
        for branch in self.branches:
            for child in branch.steps:
                yield from child.iter_nodes()


class PlanUpdate(BaseModel):
    step_id: UUID
    args: dict[str, Any]
    reason: str = ""
    replace: bool = False


class Plan(BaseModel):
    plan_id: UUID = Field(default_factory=uuid4)
    goal_id: UUID
    steps: list[PlanStep]
    plan_kind: Literal["TEMPORARY", "RECOVERY", "BUILDING", "SKILL"] = "TEMPORARY"
    status: Literal[
        "PENDING", "RUNNING", "PAUSED", "DONE", "BLOCKED", "PREEMPTED", "ABORTED",
        "TIMEOUT", "NEEDS_REVALIDATION",
    ] = "PENDING"
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    created_game_day: int = 0
    current_step_index: int = 0
    max_duration_ticks: int = Field(72000, ge=1, le=72000)
    revision: int = 1

    def iter_nodes(self):
        for step in self.steps:
            yield from step.iter_nodes()

    def update_pending_step(self, update: PlanUpdate) -> bool:
        for step in self.iter_nodes():
            if step.step_id != update.step_id:
                continue
            if step.status not in {StepStatus.PENDING, StepStatus.PAUSED, StepStatus.NEEDS_REVALIDATION}:
                return False
            step.args = dict(update.args) if update.replace else {**step.args, **update.args}
            step.status = StepStatus.PENDING
            step.request_id = None
            step.last_error_code = None
            self.revision += 1
            self.checkpoint["last_update_reason"] = update.reason
            return True
        return False


PlanBranch.model_rebuild()
PlanStep.model_rebuild()
