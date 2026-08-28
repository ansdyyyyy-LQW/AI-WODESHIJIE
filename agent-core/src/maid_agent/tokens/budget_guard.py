from __future__ import annotations

from dataclasses import dataclass

from maid_agent.config import RuntimeBudgetSettings, RndBudgetSettings
from maid_agent.tokens.ledger import TokenLedger


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RndBudgetCheckpoint:
    cycle_id: str
    used: int
    budget: int
    remaining: int
    ratio: float
    checkpoint: str
    reevaluate_direction: bool
    scope_to_result: bool
    finish_only: bool
    force_close: bool

    def as_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "used": self.used,
            "budget": self.budget,
            "remaining": self.remaining,
            "ratio": self.ratio,
            "checkpoint": self.checkpoint,
            "reevaluate_direction": self.reevaluate_direction,
            "scope_to_result": self.scope_to_result,
            "finish_only": self.finish_only,
            "force_close": self.force_close,
        }


@dataclass
class BudgetGuard:
    ledger: TokenLedger
    runtime_settings: RuntimeBudgetSettings
    rnd_settings: RndBudgetSettings

    def check_runtime(self, *, game_day: int | None, estimated_request_tokens: int) -> None:
        settings = self.runtime_settings
        if not settings.enabled:
            return
        reserve = max(settings.reserve_tokens, estimated_request_tokens)
        if settings.max_per_game_day is not None and game_day is not None:
            if self.ledger.total("runtime", game_day=game_day) + reserve > settings.max_per_game_day:
                raise BudgetExceeded("RUNTIME_DAY_BUDGET_EXCEEDED", "当前游戏日的日常 AI 额度不足")
        if settings.max_per_real_hour is not None:
            if self.ledger.total("runtime", real_hour=True) + reserve > settings.max_per_real_hour:
                raise BudgetExceeded("RUNTIME_HOUR_BUDGET_EXCEEDED", "当前一小时的日常 AI 额度不足")

    def rnd_checkpoint(self, *, cycle_id: str, budget: int | None = None) -> RndBudgetCheckpoint:
        with self.ledger.store.connection() as conn:
            row = conn.execute(
                "SELECT token_budget FROM rnd_cycles WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
        if row is not None:
            # A cycle owns the budget captured at creation. Live settings cannot change it.
            actual_budget = max(1, int(row["token_budget"]))
        elif budget is not None:
            actual_budget = max(1, int(budget))
        else:
            raise BudgetExceeded("RND_CYCLE_UNKNOWN", "找不到当前研发周期的锁定预算")
        used = self.ledger.total("rnd", cycle_id=cycle_id)
        ratio = used / actual_budget
        if ratio >= .95:
            checkpoint = "FORCE_CLOSE"
        elif ratio >= .85:
            checkpoint = "FINISH_ONLY"
        elif ratio >= .75:
            checkpoint = "SCOPE_TO_RESULT"
        elif ratio >= .50:
            checkpoint = "REEVALUATE"
        else:
            checkpoint = "NORMAL"
        return RndBudgetCheckpoint(
            cycle_id=cycle_id, used=used, budget=actual_budget,
            remaining=max(0, actual_budget-used), ratio=ratio, checkpoint=checkpoint,
            reevaluate_direction=ratio >= .50, scope_to_result=ratio >= .75,
            finish_only=ratio >= .85, force_close=ratio >= .95,
        )

    def check_rnd(
        self, *, cycle_id: str | None, estimated_request_tokens: int, purpose: str = "rnd_work",
        cycle_budget: int | None = None,
    ) -> RndBudgetCheckpoint:
        if not cycle_id:
            raise BudgetExceeded("RND_CYCLE_MISSING", "AI 研发请求缺少当前周期")
        if estimated_request_tokens > self.rnd_settings.max_single_request:
            raise BudgetExceeded("RND_REQUEST_TOO_LARGE", "单次研发请求超过上限")
        checkpoint = self.rnd_checkpoint(cycle_id=cycle_id, budget=cycle_budget)
        if checkpoint.used + estimated_request_tokens > checkpoint.budget:
            raise BudgetExceeded("RND_CYCLE_BUDGET_EXCEEDED", "当前五日研发周期额度不足")

        early_scope_purposes = {"rnd_direction", "rnd_research", "rnd_design", "rnd_exploration"}
        finishing_purposes = {"rnd_repair", "rnd_build", "rnd_test", "rnd_finalize", "rnd_handoff"}
        reserve = 15_000_000 if checkpoint.budget >= 100_000_000 else max(1, int(checkpoint.budget * .15))
        if purpose in early_scope_purposes and checkpoint.used + estimated_request_tokens > checkpoint.budget - reserve:
            raise BudgetExceeded("RND_FINISH_RESERVE_PROTECTED", "已为构建、修正和收尾保留额度")
        if checkpoint.finish_only and purpose in early_scope_purposes | {"rnd_proposal"}:
            raise BudgetExceeded("RND_FINISH_ONLY", "额度已进入收尾保护阶段，不能再扩大研发范围")
        if checkpoint.force_close and purpose not in finishing_purposes:
            raise BudgetExceeded("RND_FORCE_CLOSE", "额度已到硬收尾点，必须形成完成、阶段完成或失败结果")
        return checkpoint

    def limit_rnd_request(
        self, *, cycle_id: str | None, prompt_tokens: int, purpose: str,
        desired_completion_tokens: int = 4096,
    ) -> tuple[RndBudgetCheckpoint, int]:
        """Return a hard output cap derived from this cycle's persisted remaining budget."""
        if not cycle_id:
            raise BudgetExceeded("RND_CYCLE_MISSING", "AI 研发请求缺少当前周期")
        checkpoint = self.rnd_checkpoint(cycle_id=cycle_id)
        prompt_tokens = max(1, int(prompt_tokens))
        desired_completion_tokens = max(1, int(desired_completion_tokens))
        max_single = max(1, int(self.rnd_settings.max_single_request))
        allowed_total = min(checkpoint.remaining, max_single)

        early_scope_purposes = {"rnd_direction", "rnd_research", "rnd_design", "rnd_exploration"}
        finishing_purposes = {"rnd_repair", "rnd_build", "rnd_test", "rnd_finalize", "rnd_handoff"}
        reserve = 15_000_000 if checkpoint.budget >= 100_000_000 else max(1, int(checkpoint.budget * .15))
        if purpose in early_scope_purposes:
            allowed_total = min(allowed_total, max(0, checkpoint.budget - reserve - checkpoint.used))
        if checkpoint.finish_only and purpose in early_scope_purposes | {"rnd_proposal"}:
            raise BudgetExceeded("RND_FINISH_ONLY", "额度已进入收尾保护阶段，不能再扩大研发范围")
        if checkpoint.force_close and purpose not in finishing_purposes:
            raise BudgetExceeded("RND_FORCE_CLOSE", "额度已到硬收尾点，必须形成完成、阶段完成或失败结果")

        completion_cap = min(desired_completion_tokens, allowed_total - prompt_tokens)
        if completion_cap < 1:
            raise BudgetExceeded("RND_CYCLE_BUDGET_EXCEEDED", "当前五日研发周期额度不足")
        self.check_rnd(
            cycle_id=cycle_id,
            estimated_request_tokens=prompt_tokens + completion_cap,
            purpose=purpose,
        )
        return checkpoint, completion_cap
