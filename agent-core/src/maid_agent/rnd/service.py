from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from maid_agent.control.events import EventBus
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.harness import HarnessResult, RndHarness
from maid_agent.rnd.locks import exclusive_file_lock
from maid_agent.rnd.models import (
    RndCycle, RndMode, RndOutcome, RndPhase, RndPlanningDecision,
    RndProjectSize, RndProposal,
)
from maid_agent.rnd.mod_research.service import ModResearchService
from maid_agent.rnd.orchestrator import RndOrchestrator
from maid_agent.skills.models import SkillSpec
from maid_agent.skills.store import SkillStore


log = logging.getLogger(__name__)
ACTIVE_STATUSES = {"CREATED", "RUNNING", "SUSPENDED"}
TERMINAL_OUTCOMES = {value.value for value in RndOutcome}


class RndService:
    def __init__(
        self, store: MemoryStore, skills: SkillStore, event_bus: EventBus, harness: RndHarness,
        orchestrator: RndOrchestrator | None = None, mod_research: ModResearchService | None = None,
    ):
        self.store = store
        self.skills = skills
        self.event_bus = event_bus
        self.harness = harness
        self.orchestrator = orchestrator
        self.mod_research = mod_research or ModResearchService()

    def readiness(self) -> dict[str, Any]:
        mode, missing = self.harness.readiness()
        return {
            "mode": mode, "missing": missing,
            "source_workspace": str(self.harness.source_workspace) if self.harness.source_workspace else None,
            "runner_path": self.harness.runner_path,
        }

    def recover_interrupted_cycles(self, active_cycle_ids: set[str] | None = None) -> list[RndCycle]:
        """Recover lock-free unfinished rows left by a normal stop or an earlier process."""
        active_cycle_ids = set(active_cycle_ids or ())
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rnd_cycles WHERE status IN ('CREATED','RUNNING','SUSPENDED','WAITING_USER') "
                "ORDER BY CASE WHEN status='WAITING_USER' THEN 1 ELSE 0 END,created_at,cycle_id"
            ).fetchall()
        resumable: list[RndCycle] = []
        for original in rows:
            cycle_id = str(original["cycle_id"])
            if cycle_id in active_cycle_ids:
                continue
            cycle = self._cycle_from_row(original)
            original_result = self._read_json(cycle.artifact_dir / "output" / "rnd_result.json")
            legacy_cancelled = str(original["status"]) == "WAITING_USER" and self._is_suspend_result(
                original_result, legacy_only=True,
            )
            if str(original["status"]) == "WAITING_USER" and not legacy_cancelled:
                continue
            lock_path = cycle.artifact_dir.parent / ".rnd-cycle.lock"
            with exclusive_file_lock(lock_path) as acquired:
                if not acquired:
                    self.event_bus.publish("RND_RECOVERY", {
                        "cycle_id": cycle_id, "status": "ACTIVE_LOCK_CONFIRMED",
                        "message": "检测到真实研发锁，保留当前周期",
                    })
                    continue
                with self.store.connection() as conn:
                    row = conn.execute(
                        "SELECT * FROM rnd_cycles WHERE cycle_id=?", (cycle_id,),
                    ).fetchone()
                if row is None:
                    continue
                cycle = self._cycle_from_row(row)
                saved_result = self._read_json(cycle.artifact_dir / "output" / "rnd_result.json")
                legacy_cancelled = str(row["status"]) == "WAITING_USER" and self._is_suspend_result(
                    saved_result, legacy_only=True,
                )
                if str(row["status"]) not in ACTIVE_STATUSES and not legacy_cancelled:
                    continue
                diagnostic = self._recovery_diagnostic(cycle, row)
                saved_outcome = self._terminal_outcome(saved_result)
                if saved_outcome and not self._is_suspend_result(saved_result):
                    checkpoint = dict(saved_result.get("budget") or self._checkpoint(cycle))
                    self._update(
                        cycle, status=saved_outcome, outcome=saved_outcome,
                        phase=saved_result.get("phase") or row["phase"] or RndPhase.FINALIZING,
                        summary=json.dumps(saved_result, ensure_ascii=False, default=str),
                        checkpoint=checkpoint,
                        project_state=dict(saved_result.get("project_state") or {}),
                        failure_state=dict(saved_result.get("failure_state") or {}),
                    )
                    self.event_bus.publish("RND_RECOVERY", {
                        "cycle_id": cycle_id, "status": saved_outcome,
                        "message": "数据库状态已按已有终态产物恢复",
                    })
                    continue
                if self._recoverable_input(cycle):
                    recovery_summary = {
                        "message": "检测到可恢复暂停或上次异常退出；输入与工作区仍可用，周期将从持久化阶段继续",
                        "recovery": diagnostic,
                    }
                    with self.store.connection() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if legacy_cancelled and conn.execute(
                            "SELECT 1 FROM rnd_cycles WHERE cycle_id<>? "
                            "AND status IN ('CREATED','RUNNING','SUSPENDED') LIMIT 1",
                            (cycle_id,),
                        ).fetchone():
                            continue
                        updated = conn.execute(
                            "UPDATE rnd_cycles SET status='CREATED',outcome=NULL,owner_pid=NULL,"
                            "owner_started_at=NULL,summary=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE cycle_id=? AND status=?",
                            (json.dumps(recovery_summary, ensure_ascii=False), cycle_id, str(row["status"])),
                        )
                    if updated.rowcount == 1:
                        cycle.status = "CREATED"
                        resumable.append(cycle)
                        self.event_bus.publish("RND_RECOVERY", {
                            "cycle_id": cycle_id, "status": "RESUMING",
                            "message": "旧周期已进入安全恢复队列",
                        })
                    continue
                has_stage = self._has_stage_artifacts(cycle)
                outcome = RndOutcome.STAGE_COMPLETED if has_stage else RndOutcome.FAILED
                reason = (
                    "输入不完整，无法可靠继续；已有阶段产物已保留"
                    if has_stage else "输入准备未完成，无法可靠恢复"
                )
                self._finalize_recovery(cycle, outcome, reason, diagnostic)
        return resumable

    def fail_preparation(self, cycle: RndCycle, error: BaseException) -> dict[str, Any]:
        """Close a cycle that failed before the asynchronous service could claim it."""
        return self._finalize_interruption(
            cycle, RndOutcome.FAILED, f"研发输入或交接准备失败：{error}",
            code="PREPARATION_FAILED", only_if_owned=False,
        )

    def finalize_cancelled(
        self, cycle: RndCycle, reason: str, proposal: RndProposal | None = None,
    ) -> dict[str, Any]:
        return self._suspend_cycle(cycle, reason, proposal)

    def finalize_unhandled_task(self, cycle: RndCycle, error: BaseException) -> dict[str, Any]:
        return self._finalize_interruption(
            cycle, RndOutcome.FAILED, f"R&D task 异常结束：{error}",
            code="TASK_EXCEPTION", only_if_owned=True,
        )

    async def run(self, cycle: RndCycle) -> dict[str, Any]:
        lock_path = cycle.artifact_dir.parent / ".rnd-cycle.lock"
        with exclusive_file_lock(lock_path) as acquired:
            if not acquired:
                return {
                    "ok": False, "outcome": "ALREADY_RUNNING",
                    "error": "已有研发周期正在运行，本次重复启动已拒绝",
                }
            if not self._claim_cycle(cycle):
                return {
                    "ok": False, "outcome": "ALREADY_RUNNING",
                    "error": "该研发周期已启动或已结束，不能再次启动",
                }
            return await self._run_claimed(cycle)

    async def _run_claimed(self, cycle: RndCycle) -> dict[str, Any]:
        self._update(cycle, status="RUNNING", mode=self.harness.readiness()[0], phase=RndPhase.DECIDING_DIRECTION)
        self.event_bus.publish("RND_STATUS", {
            "cycle_id": cycle.cycle_id, "status": "RUNNING", "phase": RndPhase.DECIDING_DIRECTION,
        })
        proposal = None
        planning = None
        attempts: list[dict[str, Any]] = []
        checkpoint_reviews: list[dict[str, Any]] = []
        try:
            # A process restart may leave completed model or Harness artifacts behind.
            # Reuse those durable results so recovery does not pay for the same work twice.
            planning = self._load_model(
                cycle.artifact_dir / "output" / "rnd_budget_plan.json", RndPlanningDecision,
            )
            proposal = self._load_model(
                cycle.artifact_dir / "output" / "rnd_proposal.json", RndProposal,
            )
            if proposal is not None:
                planning = proposal.planning
            elif planning is None:
                planning = await self.orchestrator.plan_cycle(cycle) if self.orchestrator else None
            if planning:
                planning = planning.fit_cycle_budget(cycle.token_budget)
                self._persist_planning(cycle, planning)
                if proposal is None:
                    proposal = RndProposal(summary="方向与本周期预算已锁定", planning=planning)
                    checkpoint, checkpoint_reviews, stop_reason = await self._apply_due_checkpoints(
                        cycle, proposal, checkpoint_reviews
                    )
                    if stop_reason:
                        return self._close_without_harness(cycle, proposal, checkpoint_reviews, "FAILED", stop_reason)
                    if checkpoint["finish_only"]:
                        return self._close_without_harness(
                            cycle, proposal, checkpoint_reviews, "FAILED",
                            "预算已进入收尾区，但尚未形成可编译的阶段成果",
                        )
                    proposal = await self.orchestrator.propose(cycle, planning)
                else:
                    proposal.planning = planning
                checkpoint, checkpoint_reviews, stop_reason = await self._apply_due_checkpoints(
                    cycle, proposal, checkpoint_reviews
                )
                if stop_reason:
                    return self._close_without_harness(cycle, proposal, checkpoint_reviews, "FAILED", stop_reason)
            else:
                checkpoint = self._checkpoint(cycle)
                self._save_checkpoint(cycle, checkpoint)

            self._update(cycle, status="RUNNING", phase=RndPhase.RESEARCHING)
            mods_output = cycle.artifact_dir / "output" / "mods"
            if (
                proposal and proposal.mod_research_queries and not checkpoint["finish_only"]
                and not (mods_output.exists() and any(mods_output.iterdir()))
            ):
                await self.mod_research.research_to_handoff(
                    proposal.mod_research_queries, mods_output
                )

            self._update(cycle, status="RUNNING", phase=RndPhase.DEVELOPING)
            checkpoint = self._checkpoint(cycle)
            self._save_checkpoint(cycle, checkpoint)
            self._update(cycle, status="RUNNING", phase=RndPhase.TESTING)
            result = await self._run_or_resume_harness(cycle, 1)
            attempts.append({"attempt": 1, "ok": result.ok, "code": result.code, "details": result.details})

            checkpoint = self._checkpoint(cycle)
            self._save_checkpoint(cycle, checkpoint)
            if (
                not result.ok and result.mode.value == "FULL_HARNESS" and proposal and self.orchestrator
                and not checkpoint["force_close"]
            ):
                self._update(cycle, status="RUNNING", phase=RndPhase.FIXING)
                repaired = self._load_model(
                    cycle.artifact_dir / "output" / "rnd_repair_01.json", RndProposal,
                )
                if repaired is None:
                    repaired = await self.orchestrator.repair(cycle, proposal, result.details)
                if repaired is not None:
                    proposal = repaired
                    checkpoint, checkpoint_reviews, stop_reason = await self._apply_due_checkpoints(
                        cycle, proposal, checkpoint_reviews
                    )
                    if stop_reason:
                        return self._close_without_harness(cycle, proposal, checkpoint_reviews, "FAILED", stop_reason)
                    result = await self._run_or_resume_harness(cycle, 2)
                    attempts.append({"attempt": 2, "ok": result.ok, "code": result.code, "details": result.details})

            self._update(cycle, status="RUNNING", phase=RndPhase.FINALIZING)
            candidate_result = (
                self._ingest_candidates(cycle.artifact_dir / "output" / "candidate_skills")
                if result.ok and result.mode.value == "FULL_HARNESS"
                else {"accepted": [], "rejected": [], "deferred": "隔离构建未成功，候选复用能力未进入生产库"}
            )
            analysis_only = (
                result.mode.value != "FULL_HARNESS" or self.orchestrator is None
                or getattr(self.orchestrator, "provider", None) is None
            )
            if analysis_only:
                outcome = RndOutcome.WAITING_USER
            elif result.ok and planning and (
                not planning.single_cycle_feasible or planning.project_size == RndProjectSize.BEYOND_CYCLE
            ):
                outcome = RndOutcome.STAGE_COMPLETED
            elif result.ok:
                outcome = RndOutcome.COMPLETED
            else:
                outcome = RndOutcome.FAILED

            checkpoint = self._checkpoint(cycle)
            project_state = self._project_state(
                cycle, proposal, result=result, attempts=attempts, checkpoint=checkpoint,
            )
            failure_state = self._failure_state(
                cycle, proposal, result=result, attempts=attempts, checkpoint=checkpoint,
            ) if outcome == RndOutcome.FAILED else {}
            summary = {
                "outcome": outcome,
                "phase": RndPhase.FINALIZING,
                "budget": checkpoint,
                "checkpoint_reviews": checkpoint_reviews,
                "harness": {
                    "ok": result.ok, "mode": result.mode, "code": result.code,
                    "summary": result.summary, "details": result.details, "attempts": attempts,
                },
                "candidate_skills": candidate_result,
                "proposal": proposal.model_dump(mode="json") if proposal else None,
                "project_state": project_state,
                "failure_state": failure_state,
            }
            self._write_result(cycle, summary)
            self._update(
                cycle, status=str(outcome), outcome=outcome, phase=RndPhase.FINALIZING,
                mode=result.mode, summary=json.dumps(summary, ensure_ascii=False, default=str),
                workspace=result.workspace, checkpoint=checkpoint, project_state=project_state,
                failure_state=failure_state,
            )
            self.event_bus.publish("RND_STATUS", {
                "cycle_id": cycle.cycle_id, "status": outcome, "phase": RndPhase.FINALIZING,
                "result": summary,
            })
            return summary
        except asyncio.CancelledError:
            # CancelledError is a BaseException on supported Python versions and is
            # intentionally finalized separately before the file-lock context exits.
            self.finalize_cancelled(cycle, "R&D task 已随软件正常停止，当前阶段可在下次启动时继续", proposal)
            raise
        except Exception as exc:
            log.exception("R&D cycle failed")
            checkpoint = self._checkpoint(cycle)
            failure_state = {
                "direction": proposal.planning.direction if proposal else "尚未完成方向选择",
                "work_completed": ["已保存五日输入", "已保存当前预算状态"],
                "actual_tokens": checkpoint["used"], "failure_reason": str(exc),
                "source_workspace": str(getattr(self.harness, "source_workspace", "") or ""),
                "artifacts": str(cycle.artifact_dir), "disproved_routes": [],
                "continuation_point": "从已保存输入、预算状态和直接错误继续",
            }
            summary = {"ok": False, "outcome": RndOutcome.FAILED, "error": str(exc), "budget": checkpoint, "failure_state": failure_state}
            self._write_result(cycle, summary)
            self._update(
                cycle, status="FAILED", outcome=RndOutcome.FAILED, phase=RndPhase.FINALIZING,
                summary=json.dumps(summary, ensure_ascii=False, default=str), checkpoint=checkpoint,
                failure_state=failure_state,
            )
            self.event_bus.publish("RND_STATUS", {
                "cycle_id": cycle.cycle_id, "status": "FAILED", "phase": RndPhase.FINALIZING,
                "error": str(exc), "failure_state": failure_state,
            })
            return summary

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _load_model(path: Path, model_type: Any) -> Any | None:
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def _load_harness_result(self, cycle: RndCycle, attempt: int) -> HarnessResult | None:
        path = cycle.artifact_dir / "output" / f"harness-attempt-{attempt:02d}" / "harness_result.json"
        raw = self._read_json(path)
        if not raw or not isinstance(raw.get("ok"), bool):
            return None
        try:
            mode = RndMode(str(raw.get("mode") or ""))
        except ValueError:
            return None
        workspace_raw = str(raw.get("workspace") or "").strip()
        return HarnessResult(
            bool(raw["ok"]), mode, str(raw.get("code") or "UNKNOWN"),
            str(raw.get("summary") or ""), Path(workspace_raw) if workspace_raw else None,
            path.parent, dict(raw.get("details") or {}),
        )

    async def _run_or_resume_harness(self, cycle: RndCycle, attempt: int) -> HarnessResult:
        previous = self._load_harness_result(cycle, attempt)
        if previous is None:
            suspended = self._is_suspend_result(
                self._read_json(cycle.artifact_dir / "output" / "rnd_result.json")
            )
            work_root = getattr(self.harness, "work_root", None)
            workspace = Path(work_root) / cycle.cycle_id / f"attempt-{attempt:02d}" / "source" if work_root else None
            resume = getattr(self.harness, "resume", None)
            if suspended and workspace is not None and workspace.exists() and callable(resume):
                return await resume(cycle, attempt=attempt)
            return await self.harness.run(cycle, attempt=attempt)
        if previous.code == "CANCELLED":
            resume = getattr(self.harness, "resume", None)
            if callable(resume):
                return await resume(cycle, attempt=attempt)
            return await self.harness.run(cycle, attempt=attempt)
        return previous

    @staticmethod
    def _terminal_outcome(result: dict[str, Any]) -> str | None:
        value = str(result.get("outcome") or "").split(".")[-1]
        return value if value in TERMINAL_OUTCOMES else None

    @staticmethod
    def _is_suspend_result(result: dict[str, Any], *, legacy_only: bool = False) -> bool:
        code = str(result.get("code") or "").upper()
        if legacy_only:
            return code == "CANCELLED"
        outcome = str(result.get("outcome") or "").split(".")[-1].upper()
        return code in {"CANCELLED", "SUSPENDED"} or outcome == "SUSPENDED"

    @staticmethod
    def _cycle_from_row(row: Any) -> RndCycle:
        return RndCycle(
            str(row["cycle_id"]), int(row["trigger_day"]),
            int(row["runtime_period_start_day"]), int(row["runtime_period_end_day"]),
            int(row["token_budget"]), Path(str(row["artifact_dir"])), str(row["status"]),
            str(row["mode"] or RndMode.READY), phase=str(row["phase"] or RndPhase.DECIDING_DIRECTION),
            outcome=row["outcome"], project_id=str(row["project_id"] or ""),
            project_size=row["project_size"],
            continuation_decision=str(row["continuation_decision"] or "NEW"),
        )

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            try:
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if not handle:
                    return False
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            except (AttributeError, OSError):
                return False
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    def _workspace_paths(self, cycle: RndCycle) -> list[str]:
        work_root = getattr(self.harness, "work_root", None)
        if not work_root:
            return []
        root = Path(work_root) / cycle.cycle_id
        if not root.exists():
            return []
        return [str(path) for path in sorted(root.glob("attempt-*/source")) if path.exists()]

    @staticmethod
    def _recoverable_input(cycle: RndCycle) -> bool:
        for root in (cycle.artifact_dir / "input", cycle.artifact_dir / "rnd-input"):
            if (root / "period_summary.json").is_file() and (root / "runtime_evidence.json").is_file():
                return True
        return False

    def _has_stage_artifacts(self, cycle: RndCycle) -> bool:
        output = cycle.artifact_dir / "output"
        durable = [
            output / "rnd_budget_plan.json", output / "rnd_proposal.json",
            output / "rnd_repair_01.json", cycle.artifact_dir / "source_manifest.json",
        ]
        return any(path.is_file() for path in durable) or bool(self._workspace_paths(cycle)) or any(
            output.glob("harness-attempt-*/harness_result.json")
        )

    def _recovery_diagnostic(self, cycle: RndCycle, row: Any) -> dict[str, Any]:
        owner_pid = int(row["owner_pid"] or 0)
        return {
            "database_status": str(row["status"]), "phase": str(row["phase"] or ""),
            "last_updated_at": str(row["updated_at"] or ""), "owner_pid": owner_pid or None,
            "owner_process_running": self._process_is_running(owner_pid),
            "current_process_pid": os.getpid(), "current_task_present": False,
            "lock_available": True, "artifact_dir_exists": cycle.artifact_dir.is_dir(),
            "handoff_input_ready": self._recoverable_input(cycle),
            "workspace_paths": self._workspace_paths(cycle),
        }

    def _finalize_recovery(
        self, cycle: RndCycle, outcome: RndOutcome, reason: str, diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoint = self._checkpoint(cycle)
        project_state = {
            "work_completed": ["已保留现有阶段产物"] if outcome == RndOutcome.STAGE_COMPLETED else [],
            "actual_tokens": checkpoint["used"], "artifacts": str(cycle.artifact_dir),
            "source_workspace": str(getattr(self.harness, "source_workspace", "") or ""),
            "workspace_paths": diagnostic.get("workspace_paths", []),
            "continuation_point": reason,
        }
        failure_state = {} if outcome == RndOutcome.STAGE_COMPLETED else {
            "work_completed": [], "actual_tokens": checkpoint["used"],
            "failure_reason": reason, "artifacts": str(cycle.artifact_dir),
            "continuation_point": "重新准备完整五日输入后开始后续周期",
            "recovery": diagnostic,
        }
        summary = {
            "outcome": outcome, "phase": cycle.phase, "budget": checkpoint,
            "recovery": diagnostic, "reason": reason, "project_state": project_state,
            "failure_state": failure_state,
        }
        self._write_result(cycle, summary)
        self._update(
            cycle, status=outcome.value, outcome=outcome, phase=cycle.phase,
            summary=json.dumps(summary, ensure_ascii=False, default=str), checkpoint=checkpoint,
            project_state=project_state, failure_state=failure_state,
        )
        self.event_bus.publish("RND_RECOVERY", {
            "cycle_id": cycle.cycle_id, "status": outcome.value, "message": reason,
        })
        return summary

    def _suspend_cycle(
        self, cycle: RndCycle, reason: str, proposal: RndProposal | None,
    ) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
        if row is None:
            return {"outcome": "MISSING", "already_final": True}
        if str(row["status"]) == "SUSPENDED":
            saved = self._read_json(cycle.artifact_dir / "output" / "rnd_result.json")
            return saved or {"outcome": "SUSPENDED", "already_suspended": True}
        if str(row["status"]) not in {"CREATED", "RUNNING"}:
            return {"outcome": str(row["status"]), "already_final": True}
        owner_pid = int(row["owner_pid"] or 0)
        if str(row["status"]) == "RUNNING" and owner_pid not in {0, os.getpid()}:
            return {"outcome": "EXTERNAL_OWNER", "owner_pid": owner_pid}
        cycle = self._cycle_from_row(row)
        checkpoint = self._checkpoint(cycle)
        if proposal is None:
            proposal = self._load_model(cycle.artifact_dir / "output" / "rnd_proposal.json", RndProposal)
        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        if proposal is not None:
            (output / "rnd_proposal.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        (output / "budget_checkpoint.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        workspaces = self._workspace_paths(cycle)
        project_state = {
            "direction": proposal.planning.direction if proposal else "",
            "work_completed": [proposal.summary] if proposal else [],
            "actual_tokens": checkpoint["used"],
            "source_workspace": str(getattr(self.harness, "source_workspace", "") or ""),
            "artifacts": str(cycle.artifact_dir), "workspace_paths": workspaces,
            "continuation_point": proposal.continuation_point if proposal else "从已保存阶段继续",
        }
        summary = {
            "ok": False, "outcome": "SUSPENDED", "code": "SUSPENDED", "phase": cycle.phase,
            "reason": reason, "budget": checkpoint, "proposal": proposal.model_dump(mode="json") if proposal else None,
            "project_state": project_state, "failure_state": {},
        }
        self._write_result(cycle, summary)
        workspace = Path(workspaces[-1]) if workspaces else None
        self._update(
            cycle, status="SUSPENDED", phase=cycle.phase,
            summary=json.dumps(summary, ensure_ascii=False, default=str), workspace=workspace,
            checkpoint=checkpoint, project_state=project_state, failure_state={}, clear_outcome=True,
        )
        self.event_bus.publish("RND_STATUS", {
            "cycle_id": cycle.cycle_id, "status": "SUSPENDED", "phase": str(cycle.phase),
            "code": "SUSPENDED", "reason": reason,
        })
        return summary

    def _finalize_interruption(
        self, cycle: RndCycle, outcome: RndOutcome, reason: str, *, code: str,
        proposal: RndProposal | None = None, only_if_owned: bool,
    ) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)).fetchone()
        if row is None or str(row["status"]) not in ACTIVE_STATUSES:
            return {"outcome": str(row["status"]) if row is not None else "MISSING", "already_final": True}
        owner_pid = int(row["owner_pid"] or 0)
        if only_if_owned and str(row["status"]) == "RUNNING" and owner_pid not in {0, os.getpid()}:
            return {"outcome": "EXTERNAL_OWNER", "owner_pid": owner_pid}
        cycle = self._cycle_from_row(row)
        checkpoint = self._checkpoint(cycle)
        if proposal is None:
            proposal = self._load_model(cycle.artifact_dir / "output" / "rnd_proposal.json", RndProposal)
        workspaces = self._workspace_paths(cycle)
        project_state = {
            "direction": proposal.planning.direction if proposal else "",
            "work_completed": [proposal.summary] if proposal else [],
            "actual_tokens": checkpoint["used"],
            "source_workspace": str(getattr(self.harness, "source_workspace", "") or ""),
            "artifacts": str(cycle.artifact_dir), "workspace_paths": workspaces,
            "continuation_point": proposal.continuation_point if proposal else "从已保存阶段继续",
        }
        failure_state = {
            "direction": project_state["direction"], "work_completed": project_state["work_completed"],
            "actual_tokens": checkpoint["used"], "failure_reason": reason,
            "source_workspace": project_state["source_workspace"],
            "artifacts": project_state["artifacts"], "workspace_paths": workspaces,
            "continuation_point": project_state["continuation_point"],
        }
        summary = {
            "ok": False, "outcome": outcome, "code": code, "phase": cycle.phase,
            "reason": reason, "budget": checkpoint, "project_state": project_state,
            "failure_state": failure_state,
        }
        self._write_result(cycle, summary)
        self._update(
            cycle, status=outcome.value, outcome=outcome, phase=cycle.phase,
            summary=json.dumps(summary, ensure_ascii=False, default=str), checkpoint=checkpoint,
            project_state=project_state, failure_state=failure_state,
        )
        self.event_bus.publish("RND_STATUS", {
            "cycle_id": cycle.cycle_id, "status": outcome.value, "phase": str(cycle.phase),
            "code": code, "reason": reason,
        })
        return summary

    def _claim_cycle(self, cycle: RndCycle) -> bool:
        """Atomically bind this run to the persisted cycle, budget, and artifact directory."""
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,token_budget,artifact_dir FROM rnd_cycles WHERE cycle_id=?",
                (cycle.cycle_id,),
            ).fetchone()
            if row is None or str(row["status"]) != "CREATED":
                return False
            updated = conn.execute(
                "UPDATE rnd_cycles SET status='RUNNING',owner_pid=?,"
                "owner_started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
                "WHERE cycle_id=? AND status='CREATED'", (os.getpid(), cycle.cycle_id),
            )
            if updated.rowcount != 1:
                return False
            cycle.token_budget = int(row["token_budget"])
            cycle.artifact_dir = Path(str(row["artifact_dir"])).resolve()
            cycle.status = "RUNNING"
            return True

    async def _apply_due_checkpoints(
        self, cycle: RndCycle, proposal: RndProposal,
        reviews: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        checkpoint = self._checkpoint(cycle)
        saved_reviews = list(checkpoint.get("reviews") or [])
        if not reviews:
            reviews = saved_reviews
        triggered = [int(value) for value in checkpoint.get("triggered_checkpoints") or []]
        rules = (
            (50, "重新判断方向价值并在必要时缩小范围"),
            (75, "确认剩余预算能形成可用成果或明确阶段成果"),
            (85, "停止新增大型范围，只允许完成、修正、编译、验证和整理"),
            (95, "强制形成终态，不再发起新的研发工作"),
        )
        for threshold, action in rules:
            checkpoint = self._checkpoint(cycle)
            checkpoint["triggered_checkpoints"] = triggered
            checkpoint["reviews"] = reviews
            if checkpoint["ratio"] < threshold / 100 or threshold in triggered:
                continue
            # Persist before any optional model call, so a retry cannot consume it twice.
            triggered.append(threshold)
            checkpoint["triggered_checkpoints"] = triggered
            checkpoint["active_threshold"] = threshold
            checkpoint["production_action"] = action
            self._save_checkpoint(cycle, checkpoint)

            if threshold in {50, 75}:
                if checkpoint["ratio"] >= .85 or self.orchestrator is None:
                    review_data = {
                        "threshold": threshold, "decision": "FINISH" if threshold == 75 else "SHRINK",
                        "direction_still_valuable": True,
                        "remaining_budget_can_form_result": checkpoint["ratio"] < .95,
                        "revised_scope": proposal.planning.current_cycle_scope,
                        "reason": "已接近收尾线，使用锁定方向直接缩小范围，不再额外消耗评审 Token",
                    }
                else:
                    review = await self.orchestrator.reassess(cycle, proposal, checkpoint)
                    review_data = {"threshold": threshold, **review.model_dump(mode="json")}
                reviews.append(review_data)
                if review_data["decision"] in {"SHRINK", "FINISH"}:
                    proposal.planning.current_cycle_scope = str(review_data["revised_scope"])
                checkpoint = self._checkpoint(cycle)
                checkpoint["triggered_checkpoints"] = triggered
                checkpoint["reviews"] = reviews
                checkpoint["production_action"] = action
                self._save_checkpoint(cycle, checkpoint)
                if not review_data["direction_still_valuable"] or review_data["decision"] == "FAIL":
                    return checkpoint, reviews, "检查点确认原方向不再值得继续"
                if threshold == 75 and not review_data["remaining_budget_can_form_result"]:
                    return checkpoint, reviews, "75% 检查点确认剩余预算无法形成可用成果"
            elif threshold == 85:
                checkpoint["allowed_work"] = ["complete", "repair", "compile", "verify", "organize"]
                self._save_checkpoint(cycle, checkpoint)
            else:
                checkpoint["forced_terminal"] = True
                self._save_checkpoint(cycle, checkpoint)
                return checkpoint, reviews, "95% 检查点已到达，周期按硬预算规则强制结束"
        checkpoint = self._checkpoint(cycle)
        checkpoint["triggered_checkpoints"] = triggered
        checkpoint["reviews"] = reviews
        self._save_checkpoint(cycle, checkpoint)
        return checkpoint, reviews, None

    def _close_without_harness(
        self, cycle: RndCycle, proposal: Any, reviews: list[dict[str, Any]], status: str, reason: str,
    ) -> dict[str, Any]:
        checkpoint = self._checkpoint(cycle)
        failure = {
            "direction": proposal.planning.direction, "work_completed": ["方向选择", "预算规划", "预算检查"],
            "actual_tokens": checkpoint["used"], "failure_reason": reason,
            "source_workspace": str(getattr(self.harness, "source_workspace", "") or ""),
            "artifacts": str(cycle.artifact_dir), "disproved_routes": proposal.disproved_routes,
            "continuation_point": proposal.continuation_point or "重新判断范围后继续",
        }
        summary = {
            "outcome": status, "budget": checkpoint, "checkpoint_reviews": reviews,
            "proposal": proposal.model_dump(mode="json"), "failure_state": failure,
        }
        self._write_result(cycle, summary)
        self._update(
            cycle, status=status, outcome=status, phase=RndPhase.FINALIZING,
            summary=json.dumps(summary, ensure_ascii=False, default=str), checkpoint=checkpoint,
            failure_state=failure,
        )
        return summary

    def _checkpoint(self, cycle: RndCycle) -> dict[str, Any]:
        with self.store.connection() as conn:
            used = int(conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE ledger='rnd' AND cycle_id=?",
                (cycle.cycle_id,),
            ).fetchone()[0])
            row = conn.execute(
                "SELECT token_budget,checkpoint_json FROM rnd_cycles WHERE cycle_id=?", (cycle.cycle_id,)
            ).fetchone()
        budget = max(1, int(row["token_budget"] if row is not None else cycle.token_budget))
        cycle.token_budget = budget
        previous: dict[str, Any] = {}
        if row is not None:
            try:
                previous = json.loads(row["checkpoint_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                previous = {}
        ratio = used / budget
        if ratio >= .95:
            name = "FORCE_CLOSE"
        elif ratio >= .85:
            name = "FINISH_ONLY"
        elif ratio >= .75:
            name = "SCOPE_TO_RESULT"
        elif ratio >= .50:
            name = "REEVALUATE"
        else:
            name = "NORMAL"
        result = {
            "used": used, "budget": budget, "remaining": max(0, budget-used),
            "ratio": ratio, "checkpoint": name, "reevaluate_direction": ratio >= .50,
            "scope_to_result": ratio >= .75, "finish_only": ratio >= .85,
            "force_close": ratio >= .95,
            "triggered_checkpoints": list(previous.get("triggered_checkpoints") or []),
            "reviews": list(previous.get("reviews") or []),
        }
        for key in ("active_threshold", "production_action", "allowed_work", "forced_terminal"):
            if key in previous:
                result[key] = previous[key]
        return result

    def _save_checkpoint(self, cycle: RndCycle, checkpoint: dict[str, Any]) -> None:
        self._update(cycle, status="RUNNING", checkpoint=checkpoint)
        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "budget_checkpoint.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.event_bus.publish("RND_BUDGET", {"cycle_id": cycle.cycle_id, **checkpoint})

    def _persist_planning(self, cycle: RndCycle, planning: Any) -> None:
        self._update(
            cycle, status="RUNNING", phase=RndPhase.DESIGNING, budget_plan=planning.model_dump(mode="json"),
            project_id=planning.project_id, project_size=planning.project_size,
            continuation_decision=planning.continuation_decision,
        )

    def _project_state(self, cycle: RndCycle, proposal: Any, *, result: Any, attempts: list[dict[str, Any]], checkpoint: dict[str, Any]) -> dict[str, Any]:
        planning = proposal.planning if proposal else None
        return {
            "project_id": planning.project_id if planning else "",
            "direction": planning.direction if planning else "",
            "current_cycle_scope": planning.current_cycle_scope if planning else "",
            "work_completed": [proposal.summary] if proposal else [],
            "actual_tokens": checkpoint["used"], "source_workspace": str(result.workspace or ""),
            "artifacts": str(cycle.artifact_dir), "attempts": attempts,
            "disproved_routes": proposal.disproved_routes if proposal else [],
            "continuation_point": proposal.continuation_point if proposal else "",
            "continuation_decision": str(planning.continuation_decision) if planning else "NEW",
        }

    def _failure_state(self, cycle: RndCycle, proposal: Any, *, result: Any, attempts: list[dict[str, Any]], checkpoint: dict[str, Any]) -> dict[str, Any]:
        project = self._project_state(cycle, proposal, result=result, attempts=attempts, checkpoint=checkpoint)
        return {
            "direction": project["direction"], "work_completed": project["work_completed"],
            "actual_tokens": checkpoint["used"], "failure_reason": result.summary or result.code,
            "source_workspace": project["source_workspace"], "artifacts": project["artifacts"],
            "disproved_routes": project["disproved_routes"],
            "continuation_point": project["continuation_point"] or "从隔离构建的首个直接错误继续",
        }

    def _write_result(self, cycle: RndCycle, summary: dict[str, Any]) -> None:
        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "rnd_result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def _ingest_candidates(self, directory: Path) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        if not directory.exists():
            return {"accepted": accepted, "rejected": rejected}
        for path in sorted(directory.glob("*.json")):
            try:
                spec = SkillSpec.model_validate_json(path.read_text(encoding="utf-8"))
                spec.status = "CANDIDATE"
                spec.created_by = "rnd"
                self.skills.validate_candidate(spec, source_path=str(path))
                self.skills.put(spec, source_path=str(path))
                accepted.append({
                    "skill_id": spec.skill_id, "version": spec.version,
                    "name": spec.name, "status": "CANDIDATE",
                })
            except Exception as exc:
                rejected.append({"file": path.name, "error": str(exc)})
        return {"accepted": accepted, "rejected": rejected}

    def _update(
        self, cycle: RndCycle, *, status: str, mode: Any | None = None, summary: str = "",
        workspace: Path | None = None, phase: Any | None = None, outcome: Any | None = None,
        budget_plan: dict[str, Any] | None = None, checkpoint: dict[str, Any] | None = None,
        project_state: dict[str, Any] | None = None, failure_state: dict[str, Any] | None = None,
        project_id: str | None = None, project_size: Any | None = None,
        continuation_decision: Any | None = None, clear_outcome: bool = False,
    ) -> None:
        values = {
            "status": status,
            "mode": str(getattr(mode, "value", mode)) if mode is not None else None,
            "summary": summary or None,
            "source_workspace": str(workspace) if workspace else None,
            "phase": str(getattr(phase, "value", phase)) if phase is not None else None,
            "outcome": str(getattr(outcome, "value", outcome)) if outcome is not None else None,
            "budget_plan_json": json.dumps(budget_plan, ensure_ascii=False, default=str) if budget_plan is not None else None,
            "checkpoint_json": json.dumps(checkpoint, ensure_ascii=False, default=str) if checkpoint is not None else None,
            "project_state_json": json.dumps(project_state, ensure_ascii=False, default=str) if project_state is not None else None,
            "failure_state_json": json.dumps(failure_state, ensure_ascii=False, default=str) if failure_state is not None else None,
            "project_id": project_id,
            "project_size": str(getattr(project_size, "value", project_size)) if project_size is not None else None,
            "continuation_decision": str(getattr(continuation_decision, "value", continuation_decision)) if continuation_decision is not None else None,
        }
        assignments = ["status=?", "updated_at=CURRENT_TIMESTAMP"]
        args: list[Any] = [status]
        if status != "RUNNING":
            assignments.extend(["owner_pid=NULL", "owner_started_at=NULL"])
        if clear_outcome:
            assignments.append("outcome=NULL")
        for key, value in values.items():
            if key == "status" or value is None:
                continue
            assignments.append(f"{key}=?")
            args.append(value)
        args.append(cycle.cycle_id)
        with self.store.connection() as conn:
            conn.execute(f"UPDATE rnd_cycles SET {','.join(assignments)} WHERE cycle_id=?", args)
        cycle.status = status
        if phase is not None:
            cycle.phase = phase
        if outcome is not None:
            cycle.outcome = outcome
        elif clear_outcome:
            cycle.outcome = None
