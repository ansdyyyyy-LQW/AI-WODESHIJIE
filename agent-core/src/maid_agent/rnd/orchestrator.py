from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from maid_agent.llm.provider import LLMProvider
from maid_agent.prompts.loader import load_prompt
from maid_agent.rnd.models import (
    RndCheckpointReview, RndCycle, RndPlanningDecision, RndProposal, default_planning_decision,
)


T = TypeVar("T", bound=BaseModel)


class RndOrchestrator:
    def __init__(self, provider: LLMProvider | None, source_workspace: Path | None):
        self.provider = provider
        self.source_workspace = Path(source_workspace) if source_workspace else None

    def _source_context(self, evidence: dict[str, Any], max_chars: int = 240_000) -> list[dict[str, str]]:
        if self.source_workspace is None or not self.source_workspace.exists():
            return []
        signals = []
        for event in evidence.get("events", []):
            payload = event.get("payload") or {}
            signals.extend((str(event.get("type", "")), str(payload.get("code", "")), str(payload.get("tool", ""))))
        tokens = {token.lower() for value in signals for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value)}
        scored = []
        for path in self.source_workspace.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".java", ".kt", ".toml", ".json", ".md", ".yml", ".yaml"}:
                continue
            if any(part in {".git", "build", "dist", ".gradle", "node_modules", "__pycache__"} for part in path.parts):
                continue
            relative = path.relative_to(self.source_workspace).as_posix().lower()
            score = sum(3 for token in tokens if token in relative)
            scored.append((score, path))
        scored.sort(key=lambda row: (-row[0], row[1].as_posix()))
        result: list[dict[str, str]] = []
        used = 0
        for score, path in scored:
            if used >= max_chars:
                break
            if score <= 0 and len(result) >= 80:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:20_000]
            except OSError:
                continue
            used += len(text)
            result.append({"path": path.relative_to(self.source_workspace).as_posix(), "content": text})
        return result

    @staticmethod
    def _parse_model(text: str, model: type[T]) -> T:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return model.model_validate_json(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                raise
            return model.model_validate_json(match.group(0))

    async def plan(self, cycle: RndCycle, evidence: dict[str, Any]) -> RndPlanningDecision:
        """Direction and budget are locked before any source patch is requested."""
        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        if self.provider is None:
            planning = default_planning_decision().fit_cycle_budget(cycle.token_budget)
        else:
            planning_payload = {
                "cycle": cycle.as_dict(),
                "past_five_days": evidence,
                "decision_order": [
                    "read_period", "choose_direction", "judge_value", "judge_size",
                    "judge_single_cycle_result", "set_budget", "then_allow_development",
                ],
                "budget": {
                    "maximum_not_target": cycle.token_budget,
                    "default_reference": {
                        "direction_selection": 8_000_000, "research": 12_000_000,
                        "design": 10_000_000, "development": 45_000_000,
                        "build_and_fix": 15_000_000, "finish_and_reserve": 10_000_000,
                    },
                    "minimum_finish_reserve_for_formal_cycle": 15_000_000,
                    "checkpoints": [50, 75, 85, 95],
                },
                "project_history_rule": "Compare continuing, pausing, abandoning, resuming, or starting independently; prior spending alone is never a reason to continue.",
                "background_rule": "All history and capability gaps are optional context, never mandatory direction.",
                "output_contract": RndPlanningDecision.model_json_schema(),
            }
            response = await self.provider.complete(
                [
                    {"role": "system", "content": load_prompt("rnd_system")},
                    {"role": "user", "content": json.dumps(planning_payload, ensure_ascii=False, default=str)},
                ],
                model_role="rnd_direction_and_budget", purpose="rnd_direction",
                response_schema=RndPlanningDecision.model_json_schema(),
            )
            planning = self._parse_model(response.content, RndPlanningDecision).fit_cycle_budget(cycle.token_budget)
        if cycle.token_budget >= 100_000_000 and planning.finish_reserve_tokens < 15_000_000:
            planning = planning.model_copy(update={"finish_reserve_tokens": 15_000_000})
        (output / "rnd_budget_plan.json").write_text(planning.model_dump_json(indent=2), encoding="utf-8")
        return planning

    def _load_evidence(self, cycle: RndCycle) -> tuple[Path, dict[str, Any]]:
        input_dir = cycle.artifact_dir / "input"
        if not input_dir.exists():
            input_dir = cycle.artifact_dir / "rnd-input"
        evidence_path = input_dir / "runtime_evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        return input_dir, evidence

    async def plan_cycle(self, cycle: RndCycle) -> RndPlanningDecision:
        _, evidence = self._load_evidence(cycle)
        return await self.plan(cycle, evidence)

    async def propose(self, cycle: RndCycle, planning: RndPlanningDecision | None = None) -> RndProposal | None:
        input_dir, evidence = self._load_evidence(cycle)

        planning = (planning or await self.plan(cycle, evidence)).fit_cycle_budget(cycle.token_budget)
        source_context = self._source_context(evidence)
        if self.provider is None:
            proposal = RndProposal(
                summary="独立研发模型尚未配置；本轮已完成资料整理、方向占位和预算保护，等待用户配置后继续。",
                planning=planning,
                evidence=[f"events={len(evidence.get('events', []))}", f"source_files={len(source_context)}"],
            )
        else:
            input_payload = {
                "cycle": cycle.as_dict(),
                "locked_planning_decision": planning.model_dump(mode="json"),
                "runtime_evidence": evidence,
                "source_files": source_context,
                "output_contract": RndProposal.model_json_schema(),
                "constraints": [
                    "Work only inside the supplied isolated source workspace.",
                    "The locked direction and current-cycle scope were decided before development; do not silently expand them.",
                    "Capability gaps, failures, threat history, and prior projects are optional context, not mandatory tasks.",
                    "A candidate reusable behavior remains non-production until separately approved.",
                    "External additions may only be handed off and must not be installed automatically.",
                    "Return the smallest direct verification commands that establish the chosen cycle result.",
                    "Do not introduce a concrete outcome merely as an example or hidden recommendation.",
                ],
            }
            response = await self.provider.complete(
                [
                    {"role": "system", "content": load_prompt("rnd_system")},
                    {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False, default=str)},
                ],
                model_role="rnd_proposal", purpose="rnd_proposal",
                response_schema=RndProposal.model_json_schema(),
            )
            proposal = self._parse_model(response.content, RndProposal)
            proposal.planning = planning

        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "rnd_proposal.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        change = {
            "cycle_id": cycle.cycle_id, "summary": proposal.summary,
            "planning": planning.model_dump(mode="json"), "unified_diff": proposal.unified_diff,
            "verification_commands": proposal.verification_commands,
        }
        (input_dir / "change_request.json").write_text(json.dumps(change, ensure_ascii=False, indent=2), encoding="utf-8")
        candidate_dir = output / "candidate_skills"
        candidate_dir.mkdir(exist_ok=True)
        for index, spec in enumerate(proposal.candidate_skills):
            spec = dict(spec)
            spec["status"] = "CANDIDATE"
            spec.setdefault("created_by", "rnd")
            (candidate_dir / f"candidate_{index+1:03d}.json").write_text(
                json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return proposal

    async def repair(self, cycle: RndCycle, previous: RndProposal, failure: dict[str, Any]) -> RndProposal | None:
        """One bounded finish-oriented correction after a direct build failure."""
        if self.provider is None:
            return None
        error_text = str(failure.get("error_summary") or failure.get("error") or "")[:16_000]
        source_context = self._source_context(
            {"events": [{"type": "BUILD_FAILURE", "payload": {"code": error_text, "tool": "compile"}}]},
            max_chars=100_000,
        )
        payload = {
            "cycle": cycle.as_dict(),
            "locked_planning_decision": previous.planning.model_dump(mode="json"),
            "previous_patch": previous.unified_diff[:80_000],
            "previous_proposal": previous.model_dump(mode="json"),
            "direct_failure": {**failure, "error_summary": error_text},
            "relevant_source_files": source_context,
            "output_contract": RndProposal.model_json_schema(),
            "constraints": [
                "Return one complete replacement diff against the original isolated baseline.",
                "Correct only the first directly related build or test failure.",
                "Do not expand the chosen scope or start a new large feature.",
                "This is the only automatic correction; another failure must close with saved state.",
            ],
        }
        response = await self.provider.complete(
            [
                {"role": "system", "content": load_prompt("rnd_system")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            model_role="rnd_repair", purpose="rnd_repair", response_schema=RndProposal.model_json_schema(),
        )
        proposal = self._parse_model(response.content, RndProposal)
        proposal.planning = previous.planning
        input_dir = cycle.artifact_dir / "input"
        if not input_dir.exists():
            input_dir = cycle.artifact_dir / "rnd-input"
        change = {
            "cycle_id": cycle.cycle_id, "summary": proposal.summary,
            "planning": proposal.planning.model_dump(mode="json"), "unified_diff": proposal.unified_diff,
            "verification_commands": proposal.verification_commands, "repair_of": "initial_patch",
        }
        (input_dir / "change_request.json").write_text(json.dumps(change, ensure_ascii=False, indent=2), encoding="utf-8")
        output = cycle.artifact_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "rnd_repair_01.json").write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        return proposal

    async def reassess(
        self, cycle: RndCycle, proposal: RndProposal, checkpoint: dict[str, Any]
    ) -> RndCheckpointReview:
        if self.provider is None:
            return RndCheckpointReview(
                decision="FINISH" if checkpoint.get("scope_to_result") else "CONTINUE",
                direction_still_valuable=True,
                remaining_budget_can_form_result=True,
                revised_scope=proposal.planning.current_cycle_scope,
                reason="没有独立研发模型，按已锁定范围进入确定性收尾",
            )
        payload = {
            "cycle": cycle.as_dict(), "checkpoint": checkpoint,
            "locked_planning_decision": proposal.planning.model_dump(mode="json"),
            "work_so_far": proposal.model_dump(mode="json"),
            "rules": [
                "At 50 percent, reconsider feasibility and whether scope should shrink.",
                "At 75 percent, the remaining budget must form a usable result or clear stage result.",
                "Do not add a new direction or large feature during checkpoint review.",
            ],
            "output_contract": RndCheckpointReview.model_json_schema(),
        }
        response = await self.provider.complete(
            [
                {"role": "system", "content": load_prompt("rnd_system")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            model_role="rnd_checkpoint_review", purpose="rnd_design",
            response_schema=RndCheckpointReview.model_json_schema(),
        )
        return self._parse_model(response.content, RndCheckpointReview)
