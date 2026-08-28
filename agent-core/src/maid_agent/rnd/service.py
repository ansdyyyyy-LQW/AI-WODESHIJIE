from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from maid_agent.control.events import EventBus
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.api_budget_proxy import RndApiBudgetProxy
from maid_agent.rnd.dsh_adapter import DeepSeekHarnessAdapter
from maid_agent.rnd.harness import HarnessResult, RndHarness
from maid_agent.rnd.handoff import HandoffBuilder
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
DSH_PHASES = ("RESEARCH", "DESIGN", "DEVELOPMENT", "BUILD_FIX", "FINALIZE")
DSH_PHASE_TO_RND = {
    "RESEARCH": RndPhase.RESEARCHING,
    "DESIGN": RndPhase.DESIGNING,
    "DEVELOPMENT": RndPhase.DEVELOPING,
    "BUILD_FIX": RndPhase.FIXING,
    "FINALIZE": RndPhase.FINALIZING,
}
DSH_PHASE_TASKS = {
    "RESEARCH": (
        "读取 .maidai-rnd/RND_BRIEF.md 和约束，查看目录、必要源码与依赖，确定可行路线。"
        "把简短结构化结果写到 .maidai-rnd/research_result.json，必须包含 selected_route、"
        "affected_components、known_risks、disproved_routes、next_phase_recommendation。此阶段不要扩写长报告。"
    ),
    "DESIGN": (
        "继续核对真实接口、修改边界、文件关系和最短实现顺序。把结构化结果写到 "
        ".maidai-rnd/design_result.json，然后结束本阶段；不要重新选择 R&D Director 已锁定的方向。"
    ),
    "DEVELOPMENT": (
        "在当前隔离 workspace 内真实修改允许区域的源码。不能只在回复中给 patch、方案或代码片段；"
        "需要形成可由 git diff 读取的真实文件变化，并做与修改直接相关的最小检查。"
    ),
    "BUILD_FIX": (
        "运行当前改动直接相关的 Python compile、pytest、Gradle 或项目构建，读取真实报错并持续修正。"
        "同一路线连续失败两次时改用更简单实现或复用成熟实现，不要重复同一个无效修复。"
    ),
    "FINALIZE": (
        "停止扩展范围，检查 git diff，整理源码和产物，并把简短结构化结果写到 "
        ".maidai-rnd/final_result.json。你不负责宣布最终 PASS；独立 Final Validator 会复验。"
    ),
}


class RndService:
    def __init__(
        self, store: MemoryStore, skills: SkillStore, event_bus: EventBus, harness: RndHarness,
        orchestrator: RndOrchestrator | None = None, mod_research: ModResearchService | None = None,
        dsh_adapter: DeepSeekHarnessAdapter | None = None,
        api_budget_proxy: RndApiBudgetProxy | None = None,
        handoff_builder: HandoffBuilder | None = None,
    ):
        self.store = store
        self.skills = skills
        self.event_bus = event_bus
        self.harness = harness
        self.orchestrator = orchestrator
        self.mod_research = mod_research or ModResearchService()
        self.dsh_adapter = dsh_adapter
        self.api_budget_proxy = api_budget_proxy
        self.handoff_builder = handoff_builder

    def readiness(self) -> dict[str, Any]:
        mode, missing = self.harness.readiness()
        result = {
            "mode": mode, "missing": missing,
            "source_workspace": str(self.harness.source_workspace) if self.harness.source_workspace else None,
            "runner_path": self.harness.runner_path,
        }
        if self.dsh_adapter is not None:
            result["deepseek_harness"] = self.dsh_adapter.readiness()
            result["deepseek_harness"]["api_budget_proxy"] = (
                self.api_budget_proxy.readiness() if self.api_budget_proxy is not None
                else {"available": False, "ledger": "rnd"}
            )
            if not result["deepseek_harness"]["available"]:
                result["mode"] = RndMode.ANALYSIS_ONLY
        return result

    async def check_harness_environment(self) -> dict[str, Any]:
        if self.dsh_adapter is None:
            return {"available": False, "startup": False, "missing": ["maidai_dsh_driver"]}
        ready = self.dsh_adapter.readiness()
        if not ready.get("available"):
            return {**ready, "startup": False}
        if self.dsh_adapter.process is not None and self.dsh_adapter.process.returncode is None:
            return {**ready, "startup": True, "active_cycle": True}
        probe_workspace = self.harness.work_root / ".dsh-readiness"
        return await self.dsh_adapter.probe_startup(probe_workspace)

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
        entry_phase = cycle.dsh_current_phase or RndPhase.DECIDING_DIRECTION
        self._update(cycle, status="RUNNING", mode=self.harness.readiness()[0], phase=entry_phase)
        self.event_bus.publish("RND_STATUS", {
            "cycle_id": cycle.cycle_id, "status": "RUNNING", "phase": entry_phase,
        })
        if self.dsh_adapter is not None:
            return await self._run_coding_harness_claimed(cycle)
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

    async def _run_coding_harness_claimed(self, cycle: RndCycle) -> dict[str, Any]:
        """Formal FULL_HARNESS path: director brief -> DSH -> changed workspace."""
        proposal: RndProposal | None = None
        planning: RndPlanningDecision | None = None
        workspace: Path | None = None
        session_id = cycle.dsh_session_id or f"maidai-rnd-{cycle.cycle_id}"
        resumed = bool(cycle.dsh_session_id and cycle.dsh_workspace)
        previous_event_handler: Any = None
        try:
            ready = self.dsh_adapter.readiness() if self.dsh_adapter is not None else {"available": False}
            if not ready.get("available"):
                planning = (
                    await self.orchestrator.plan_cycle(cycle)
                    if self.orchestrator is not None else None
                )
                if planning is None:
                    planning = RndPlanningDecision(
                        direction="等待 AI 研发环境可用",
                        value_reason="DeepSeek Harness 未能启动",
                        project_size=RndProjectSize.SMALL,
                        single_cycle_feasible=True,
                        intended_outcome=RndOutcome.WAITING_USER,
                        current_cycle_scope="保留当前五日输入并等待环境恢复",
                    ).fit_cycle_budget(cycle.token_budget)
                proposal = RndProposal(summary="AI研发环境无法启动", planning=planning)
                return self._close_without_harness(
                    cycle, proposal, [], "WAITING_USER",
                    "AI研发环境无法启动：" + ", ".join(ready.get("missing") or []),
                )

            persisted_proposal = self._load_model(
                cycle.artifact_dir / "output" / "rnd_proposal.json", RndProposal,
            )
            planning = (
                persisted_proposal.planning if persisted_proposal is not None else self._load_model(
                    cycle.artifact_dir / "output" / "rnd_budget_plan.json", RndPlanningDecision,
                )
            )
            if planning is None:
                planning = await self.orchestrator.plan_cycle(cycle) if self.orchestrator else None
            if planning is None:
                raise RuntimeError("R&D Director 未能生成研发方向与预算")
            planning = planning.fit_cycle_budget(cycle.token_budget)
            self._persist_planning(cycle, planning)
            proposal = persisted_proposal or RndProposal(
                summary="方向、范围与预算已由 R&D Director 锁定", planning=planning,
            )
            output_root = cycle.artifact_dir / "output"
            output_root.mkdir(parents=True, exist_ok=True)
            (output_root / "rnd_proposal.json").write_text(
                proposal.model_dump_json(indent=2), encoding="utf-8"
            )
            if planning.intended_outcome == RndOutcome.WAITING_USER:
                return self._close_without_harness(
                    cycle, proposal, [], "WAITING_USER", "独立 R&D API 尚未配置或不可用",
                )

            state = self.harness.prepare_workspace(cycle, resume=resumed)
            workspace = Path(state["workspace"])
            if resumed and Path(cycle.dsh_workspace).resolve() != workspace.resolve():
                raise RuntimeError("SUSPENDED cycle workspace does not match its persisted DSH workspace")
            checkpoint = self._checkpoint(cycle)
            brief, base_task = self.orchestrator.prepare_coding_harness(
                cycle, planning, workspace, baseline_state=state,
                token_used=int(checkpoint["used"]),
            )
            development = cycle.artifact_dir / "output" / "development"
            development.mkdir(parents=True, exist_ok=True)
            (development / "brief.json").write_text(brief.model_dump_json(indent=2), encoding="utf-8")
            ready = self.dsh_adapter.readiness() if self.dsh_adapter is not None else {}
            progress = dict(cycle.dsh_phase_progress)
            completed = [
                str(item) for item in progress.get("completed", []) if str(item) in DSH_PHASES
            ]
            phase_results = [
                dict(item) for item in progress.get("results", []) if isinstance(item, dict)
            ]
            current_phase = (
                cycle.dsh_current_phase
                if cycle.dsh_current_phase in DSH_PHASES and cycle.dsh_current_phase not in completed
                else next((phase for phase in DSH_PHASES if phase not in completed), "FINALIZE")
            )
            progress.update({
                "phase": current_phase, "state": "running", "resumed": resumed,
                "completed": completed, "results": phase_results,
            })
            self._update(
                cycle, status="RUNNING", phase=DSH_PHASE_TO_RND[current_phase], workspace=workspace,
                dsh_session_id=session_id, dsh_version=str(ready.get("version") or ""),
                dsh_profile_version=str(ready.get("profile_version") or ""),
                dsh_cli_version=str(ready.get("cli_version") or ""),
                dsh_workspace=str(workspace), dsh_current_phase=current_phase,
                dsh_phase_progress=progress,
                baseline_commit=str(state.get("baseline_commit") or cycle.baseline_commit),
                touch_dsh_event=True,
            )
            assert self.dsh_adapter is not None
            if self.api_budget_proxy is not None:
                model_environment = await self.api_budget_proxy.start(
                    cycle_id=cycle.cycle_id, phase=current_phase,
                )
                self.dsh_adapter.set_model_environment(model_environment)
            elif isinstance(self.dsh_adapter, DeepSeekHarnessAdapter):
                raise RuntimeError("R&D API 预算代理不可用，已阻止 DSH 绕过 rnd TokenLedger")

            active_phase = current_phase
            if hasattr(self.dsh_adapter, "event_handler"):
                previous_event_handler = self.dsh_adapter.event_handler

                def persist_event(event: dict[str, Any]) -> None:
                    if callable(previous_event_handler):
                        previous_event_handler(event)
                    event_progress = dict(cycle.dsh_phase_progress)
                    event_progress.update({
                        "phase": active_phase,
                        "state": str(event.get("status") or event.get("event") or "active"),
                        "session_ready": bool(event.get("session_id")),
                    })
                    self._update(
                        cycle, status="RUNNING", dsh_phase_progress=event_progress,
                        dsh_current_phase=active_phase, touch_dsh_event=True,
                    )

                self.dsh_adapter.event_handler = persist_event

            checkpoint_reviews = list(checkpoint.get("reviews") or [])
            driver_attached = False
            force_closed = False
            all_ok = True
            last_code = "SUCCESS"
            last_summary = "所有 DSH 阶段已完成"
            total_usage: dict[str, int] = {}
            for phase_name in DSH_PHASES:
                if phase_name in completed:
                    continue
                checkpoint, checkpoint_reviews, stop_reason = await self._apply_due_checkpoints(
                    cycle, proposal, checkpoint_reviews,
                )
                if checkpoint["force_close"] and phase_name != "FINALIZE":
                    force_closed = True
                    continue
                if stop_reason and not checkpoint["force_close"]:
                    all_ok = False
                    last_code = "CHECKPOINT_STOP"
                    last_summary = stop_reason
                    break

                active_phase = phase_name
                current_phase = phase_name
                progress.update({
                    "phase": phase_name, "state": "running", "completed": completed,
                    "results": phase_results,
                })
                self._update(
                    cycle, status="RUNNING", phase=DSH_PHASE_TO_RND[phase_name],
                    dsh_current_phase=phase_name, dsh_phase_progress=progress,
                    touch_dsh_event=True,
                )
                self.event_bus.publish("RND_STATUS", {
                    "cycle_id": cycle.cycle_id, "status": "RUNNING",
                    "phase": DSH_PHASE_TO_RND[phase_name], "dsh_phase": phase_name,
                })
                if self.api_budget_proxy is not None:
                    self.api_budget_proxy.set_phase(phase_name)
                restriction = ""
                if checkpoint["finish_only"]:
                    restriction = (
                        "\n预算已到 85% 收尾区：不得新增大型范围或继续扩展，只能完成现有实现、"
                        "修复、编译、验证和整理。"
                    )
                phase_task = (
                    (base_task + "\n\n" if not resumed and not driver_attached else "")
                    + f"当前阶段：{phase_name}。"
                    + ("这是同一 cycle、同一 session、同一 workspace 的恢复；不要重新选方向。" if resumed and not driver_attached else "")
                    + DSH_PHASE_TASKS[phase_name]
                    + f"\n当前锁定范围：{proposal.planning.current_cycle_scope}"
                    + restriction
                )
                if not driver_attached:
                    command = self.dsh_adapter.resume if resumed else self.dsh_adapter.start_cycle
                else:
                    command = self.dsh_adapter.run_phase
                dsh = await command(
                    session_id=session_id, workspace=workspace,
                    task=phase_task, phase=phase_name,
                )
                driver_attached = True
                phase_result = {
                    "phase": phase_name, "ok": dsh.ok, "code": dsh.code,
                    "finish_reason": dsh.finish_reason, "summary": dsh.summary,
                    "usage": dsh.usage,
                }
                phase_results.append(phase_result)
                for key, value in dsh.usage.items():
                    total_usage[key] = total_usage.get(key, 0) + int(value)
                (development / f"dsh_{phase_name.lower()}.json").write_text(
                    json.dumps({**phase_result, "events": dsh.events}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                last_code = dsh.code
                last_summary = dsh.summary
                if dsh.ok:
                    completed.append(phase_name)
                progress.update({
                    "state": "finished" if dsh.ok else "failed",
                    "finish_reason": dsh.finish_reason, "session_ready": True,
                    "completed": completed, "results": phase_results,
                })
                self._update(
                    cycle, status="RUNNING", dsh_session_id=session_id,
                    dsh_workspace=str(workspace), dsh_current_phase=phase_name,
                    dsh_phase_progress=progress, dsh_last_finish_reason=dsh.finish_reason,
                    touch_dsh_event=True,
                )
                if not dsh.ok:
                    all_ok = False
                    break
                checkpoint, checkpoint_reviews, stop_reason = await self._apply_due_checkpoints(
                    cycle, proposal, checkpoint_reviews,
                )
                if checkpoint["force_close"]:
                    force_closed = True
                elif stop_reason:
                    all_ok = False
                    last_code = "CHECKPOINT_STOP"
                    last_summary = stop_reason
                    break

            progress.update({
                "state": "completed" if all_ok else "failed", "completed": completed,
                "results": phase_results, "force_closed": force_closed,
            })
            self._update(
                cycle, status="RUNNING", dsh_phase_progress=progress,
                dsh_current_phase=current_phase,
                dsh_last_finish_reason="completed" if all_ok else cycle.dsh_last_finish_reason,
                touch_dsh_event=True,
            )
            (development / "dsh_result.json").write_text(json.dumps({
                "ok": all_ok, "code": last_code, "summary": last_summary,
                "session_id": session_id, "workspace": str(workspace),
                "completed_phases": completed, "phase_results": phase_results,
                "usage": total_usage, "force_closed": force_closed,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            checkpoint = self._checkpoint(cycle)
            validator = await self.harness.validate_workspace(
                cycle, baseline_commit=str(state.get("baseline_commit") or cycle.baseline_commit),
            )
            validator_attempt = {
                "phase": "FINAL_VALIDATOR", "ok": validator.ok, "code": validator.code,
                "summary": validator.summary,
            }
            decision_result = validator
            if not all_ok and validator.ok:
                decision_result = HarnessResult(
                    False, RndMode.FULL_HARNESS, last_code, last_summary,
                    workspace, development,
                    {"session_id": session_id, "completed_phases": completed},
                )
            if not all_ok or not validator.ok:
                outcome = RndOutcome.FAILED
            elif force_closed or planning.intended_outcome == RndOutcome.STAGE_COMPLETED:
                outcome = RndOutcome.STAGE_COMPLETED
            else:
                outcome = RndOutcome.COMPLETED
            project_state = self._project_state(
                cycle, proposal, result=decision_result,
                attempts=[*phase_results, validator_attempt],
                checkpoint=checkpoint,
            )
            failure_state = self._failure_state(
                cycle, proposal, result=decision_result,
                attempts=[*phase_results, validator_attempt],
                checkpoint=checkpoint,
            ) if outcome == RndOutcome.FAILED else {}
            summary = {
                "outcome": outcome,
                "phase": RndPhase.FINALIZING,
                "budget": checkpoint,
                "development_brief": brief.model_dump(mode="json"),
                "deepseek_harness": {
                    "ok": all_ok, "code": last_code, "summary": last_summary,
                    "session_id": session_id, "usage": total_usage,
                    "completed_phases": completed, "phase_results": phase_results,
                    "force_closed": force_closed,
                },
                "final_validator": {
                    "ok": validator.ok, "code": validator.code,
                    "summary": validator.summary, "workspace": str(validator.workspace or ""),
                    "output_dir": str(validator.output_dir), "details": validator.details,
                },
                "checkpoint_reviews": checkpoint_reviews,
                "proposal": proposal.model_dump(mode="json"),
                "project_state": project_state,
                "failure_state": failure_state,
            }
            self._write_result(cycle, summary)
            if self.handoff_builder is not None:
                self.handoff_builder.finalize_cycle(cycle, summary)
            self._update(
                cycle, status=outcome.value, outcome=outcome, phase=RndPhase.FINALIZING,
                mode=RndMode.FULL_HARNESS, summary=json.dumps(summary, ensure_ascii=False, default=str),
                workspace=workspace, checkpoint=checkpoint, project_state=project_state,
                failure_state=failure_state,
            )
            self.event_bus.publish("RND_STATUS", {
                "cycle_id": cycle.cycle_id, "status": outcome.value,
                "phase": RndPhase.FINALIZING, "result": summary,
            })
            return summary
        except asyncio.CancelledError:
            if self.dsh_adapter is not None:
                with suppress(Exception):
                    suspended = await asyncio.wait_for(
                        asyncio.shield(self.dsh_adapter.suspend()), timeout=30,
                    )
                    if suspended is not None:
                        self._update(
                            cycle, status="RUNNING", dsh_last_finish_reason="suspended",
                            dsh_phase_progress={**cycle.dsh_phase_progress, "state": "suspended"},
                            touch_dsh_event=True,
                        )
            self.finalize_cancelled(
                cycle, "R&D task 已安全暂停；DSH session 与 workspace 已保留", proposal,
            )
            raise
        except Exception as exc:
            log.exception("DSH R&D cycle failed")
            checkpoint = self._checkpoint(cycle)
            summary = {
                "ok": False, "outcome": RndOutcome.FAILED,
                "code": "DSH_CYCLE_FAILED", "error": str(exc), "budget": checkpoint,
                "session_id": session_id, "workspace": str(workspace or ""),
            }
            self._write_result(cycle, summary)
            if self.handoff_builder is not None:
                with suppress(Exception):
                    self.handoff_builder.finalize_cycle(cycle, summary)
            self._update(
                cycle, status="FAILED", outcome=RndOutcome.FAILED,
                phase=RndPhase.FINALIZING,
                summary=json.dumps(summary, ensure_ascii=False, default=str),
                workspace=workspace, checkpoint=checkpoint,
            )
            return summary
        finally:
            if self.dsh_adapter is not None and hasattr(self.dsh_adapter, "event_handler"):
                self.dsh_adapter.event_handler = previous_event_handler
            if self.dsh_adapter is not None:
                with suppress(Exception):
                    await self.dsh_adapter.terminate()
            if self.api_budget_proxy is not None:
                with suppress(Exception):
                    await self.api_budget_proxy.close()

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
        try:
            dsh_phase_progress = json.loads(row["dsh_phase_progress_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            dsh_phase_progress = {}
        return RndCycle(
            str(row["cycle_id"]), int(row["trigger_day"]),
            int(row["runtime_period_start_day"]), int(row["runtime_period_end_day"]),
            int(row["token_budget"]), Path(str(row["artifact_dir"])), str(row["status"]),
            str(row["mode"] or RndMode.READY), phase=str(row["phase"] or RndPhase.DECIDING_DIRECTION),
            outcome=row["outcome"], project_id=str(row["project_id"] or ""),
            project_size=row["project_size"],
            continuation_decision=str(row["continuation_decision"] or "NEW"),
            dsh_session_id=str(row["dsh_session_id"] or ""),
            dsh_version=str(row["dsh_version"] or ""),
            dsh_profile_version=str(row["dsh_profile_version"] or ""),
            dsh_cli_version=str(row["dsh_cli_version"] or ""),
            dsh_workspace=str(row["dsh_workspace"] or ""),
            dsh_current_phase=str(row["dsh_current_phase"] or ""),
            dsh_phase_progress=dsh_phase_progress,
            dsh_last_finish_reason=str(row["dsh_last_finish_reason"] or ""),
            dsh_last_event_at=row["dsh_last_event_at"],
            baseline_commit=str(row["baseline_commit"] or ""),
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
        paths = [root / "source", *sorted(root.glob("attempt-*/source"))]
        return [str(path) for path in paths if path.exists()]

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
        dsh_session_id: str | None = None, dsh_version: str | None = None,
        dsh_profile_version: str | None = None, dsh_cli_version: str | None = None,
        dsh_workspace: str | None = None, dsh_current_phase: str | None = None,
        dsh_phase_progress: dict[str, Any] | None = None,
        dsh_last_finish_reason: str | None = None, baseline_commit: str | None = None,
        touch_dsh_event: bool = False,
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
            "dsh_session_id": dsh_session_id,
            "dsh_version": dsh_version,
            "dsh_profile_version": dsh_profile_version,
            "dsh_cli_version": dsh_cli_version,
            "dsh_workspace": dsh_workspace,
            "dsh_current_phase": dsh_current_phase,
            "dsh_phase_progress_json": json.dumps(dsh_phase_progress, ensure_ascii=False, default=str) if dsh_phase_progress is not None else None,
            "dsh_last_finish_reason": dsh_last_finish_reason,
            "baseline_commit": baseline_commit,
        }
        assignments = ["status=?", "updated_at=CURRENT_TIMESTAMP"]
        args: list[Any] = [status]
        if status != "RUNNING":
            assignments.extend(["owner_pid=NULL", "owner_started_at=NULL"])
        if clear_outcome:
            assignments.append("outcome=NULL")
        if touch_dsh_event:
            assignments.append("dsh_last_event_at=CURRENT_TIMESTAMP")
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
        for name, value in (
            ("dsh_session_id", dsh_session_id), ("dsh_version", dsh_version),
            ("dsh_profile_version", dsh_profile_version), ("dsh_cli_version", dsh_cli_version),
            ("dsh_workspace", dsh_workspace), ("dsh_current_phase", dsh_current_phase),
            ("dsh_phase_progress", dsh_phase_progress),
            ("dsh_last_finish_reason", dsh_last_finish_reason), ("baseline_commit", baseline_commit),
        ):
            if value is not None:
                setattr(cycle, name, value)
