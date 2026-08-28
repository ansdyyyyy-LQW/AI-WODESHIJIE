from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from maid_agent.memory.store import MemoryStore
from maid_agent.metrics.scoreboard import Scoreboard
from maid_agent.rnd.models import RndCycle
from maid_agent.skills.store import SkillStore


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class HandoffBuilder:
    """Builds the real five-day evidence package and a user-facing handoff root.

    The constructor accepts the 0.1 arguments for backward compatibility.  The
    production runtime supplies the same persisted Scoreboard through MemoryStore.
    """

    def __init__(
        self,
        store: MemoryStore,
        skills: SkillStore,
        scoreboard: Scoreboard | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.store = store
        self.skills = skills
        self.scoreboard = scoreboard or Scoreboard(store)
        self.repo_root = Path(repo_root).resolve() if repo_root else None

    def prepare_input(self, cycle: RndCycle, strategy: Any, tool_names: list[str]) -> Path:
        # Canonical production input is `input`; a byte-identical `rnd-input`
        # compatibility copy is emitted for 0.1 external Harnesses.
        root = cycle.artifact_dir / "input"
        root.mkdir(parents=True, exist_ok=True)
        events = self.store.recent_events(limit=5000, min_day=cycle.period_start_day)
        failures = [
            event
            for event in events
            if event.get("type")
            in {
                "ACTION_FAILED",
                "ACTION_STUCK",
                "ACTION_TIMEOUT",
                "ACTION_PREEMPTED",
                "MAID_DEATH",
                "GOAL_BLOCKED",
                "RUNTIME_ERROR",
            }
        ]
        strategy_value = (
            strategy.model_dump(mode="json") if hasattr(strategy, "model_dump") else strategy
        )
        period_summary = {
            "cycle_id": cycle.cycle_id,
            "start_day": cycle.period_start_day,
            "end_day": cycle.period_end_day,
            "trigger_day": cycle.trigger_day,
            "token_budget": cycle.token_budget,
            "event_count": len(events),
            "failure_count": len(failures),
            "memory": self.store.summary(),
            "scoreboard": self.scoreboard.snapshot(),
        }
        _write_json(root / "period_summary.json", period_summary)
        with (root / "event_timeline.jsonl").open("w", encoding="utf-8") as handle:
            for event in reversed(events):
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        _write_json(root / "deaths.json", [e for e in events if e.get("type") == "MAID_DEATH"])
        _write_json(root / "failed_actions.json", failures)
        _write_json(root / "combat_metrics.json", self.scoreboard.snapshot())
        _write_json(root / "resource_metrics.json", {"memory": self.store.summary()})
        _write_json(root / "goal_history.json", [e for e in events if str(e.get("type", "")).startswith("GOAL_")])
        _write_json(root / "skill_scoreboard.json", self.skills.list(status=None, limit=1000))
        _write_json(root / "skill_refinement_queue.json", self.skills.refinement_queue(limit=500))
        _write_json(root / "threat_windows.json", self.store.recent_threat_windows(limit=64))
        _write_json(root / "strategy_state.json", strategy_value)
        _write_json(root / "world_locations.json", self._locations())
        _write_json(root / "current_capabilities.json", tool_names)
        capability_gaps=self.store.list_capability_gaps(limit=500)
        _write_json(root / "capability_gaps.json", {
            "role":"background_only",
            "may_choose_other_direction":True,
            "items":capability_gaps,
        })
        project_history=self._rnd_project_history()
        _write_json(root / "rnd_project_history.json", {
            "decision_rule":"每个新周期重新比较继续、暂停、放弃、恢复或选择新方向；既有投入不是继续的唯一理由。",
            "items":project_history,
        })
        _write_json(root / "source_manifest.json", self._source_manifest())
        _write_json(
            root / "runtime_evidence.json",
            {
                "cycle": cycle.as_dict(),
                "strategy": strategy_value,
                "events": events,
                "skills": self.skills.list(status=None, limit=1000),
                "threat_windows": self.store.recent_threat_windows(limit=64),
                "tool_names": tool_names,
                "capability_gaps":{
                    "role":"background_only_not_required_direction",
                    "items":capability_gaps,
                },
                "rnd_project_history":project_history,
                "production_constraint": "只修改隔离 worktree；不得写入运行源码、Minecraft 世界或 mods 目录。",
            },
        )
        (root / "repo_commit.txt").write_text(self._git_commit(), encoding="utf-8")
        (root / "README_FOR_AGENT.md").write_text(self._instructions(cycle), encoding="utf-8")
        self.write_checksums(root)
        compatibility=cycle.artifact_dir/"rnd-input"
        if compatibility.exists():
            import shutil;shutil.rmtree(compatibility)
        import shutil;shutil.copytree(root,compatibility)
        return root

    def create_default_output(
        self,
        cycle: RndCycle,
        *,
        summary: str = "五日数据与隔离源码工作区已准备，等待 R&D Harness。",
    ) -> Path:
        root = cycle.artifact_dir
        for directory in (
            root / "artifacts",
            root / "candidate_skills",
            root / "candidate-mods",
            root / "validation",
            root / "output",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "cycle_id": cycle.cycle_id,
            "status": "READY",
            "mode": str(cycle.mode),
            "requires_user_action": False,
            "summary": summary,
            "production_version": self._production_version(),
            "source_baseline": self._git_commit(),
            "artifacts": [],
            "recommendations": [],
            "validation": {"status": "WAITING"},
        }
        _write_json(root / "handoff_manifest.json", manifest)
        (root / "RND_REPORT.md").write_text(f"# {cycle.cycle_id} R&D\n\n{summary}\n", encoding="utf-8")
        (root / "CHANGE_SUMMARY.md").write_text("# Change Summary\n\n当前尚无已验证候选变更。\n", encoding="utf-8")
        (root / "USER_ACTION_REQUIRED.md").write_text("研发完成：否\n需要人工操作：否\n", encoding="utf-8")
        (root / "candidate-mods" / "MOD_RECOMMENDATIONS.md").write_text("# Mod Recommendations\n\n暂无。\n", encoding="utf-8")
        (root / "validation" / "test-results.txt").write_text("WAITING_FOR_HARNESS\n", encoding="utf-8")
        (root / "output" / "README_研发交接.txt").write_text(
            "所有候选代码、Skill 与 Mod 只进入 Handoff；不会自动覆盖生产源码或安装到 mods。\n",
            encoding="utf-8",
        )
        self.write_checksums(root)
        return root

    @staticmethod
    def validate_manifest(path: Path) -> list[str]:
        errors: list[str] = []
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            return [f"manifest_unreadable:{exc}"]
        for key in ("cycle_id", "status", "requires_user_action", "summary", "artifacts", "recommendations"):
            if key not in data:
                errors.append(f"missing:{key}")
        root = Path(path).parent.resolve()
        for artifact in data.get("artifacts", []):
            relative = str(artifact.get("path", ""))
            candidate = Path(relative)
            if not relative or candidate.is_absolute() or ".." in candidate.parts:
                errors.append(f"unsafe_artifact_path:{relative}")
                continue
            resolved = (root / candidate).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(f"unsafe_artifact_path:{relative}")
            if artifact.get("type") == "forge_mod":
                if artifact.get("minecraft") != "1.20.1":
                    errors.append(f"wrong_minecraft:{relative}")
                if str(artifact.get("loader", "")).lower() != "forge":
                    errors.append(f"wrong_loader:{relative}")
        return errors

    @staticmethod
    def write_checksums(root: Path) -> Path:
        output = Path(root) / "checksums.sha256"
        lines: list[str] = []
        for path in sorted(p for p in Path(root).rglob("*") if p.is_file() and p != output):
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output

    def _locations(self) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM world_locations ORDER BY last_seen_tick DESC LIMIT 1000")]

    def _source_manifest(self) -> list[dict[str, Any]]:
        if self.repo_root is None or not self.repo_root.exists():
            return []
        excluded = {".git", ".venv", "build", "dist", "run", "__pycache__", ".pytest_cache", ".gradle"}
        result: list[dict[str, Any]] = []
        for path in self.repo_root.rglob("*"):
            if path.is_file() and not any(part in excluded for part in path.parts):
                result.append({"path": path.relative_to(self.repo_root).as_posix(), "size": path.stat().st_size})
        return result[:20000]

    def _rnd_project_history(self) -> list[dict[str,Any]]:
        with self.store.connection() as conn:
            rows=conn.execute(
                """SELECT cycle_id,trigger_day,status,outcome,project_id,project_size,
                          continuation_decision,budget_plan_json,checkpoint_json,project_state_json,
                          failure_state_json,summary
                   FROM rnd_cycles ORDER BY trigger_day DESC,created_at DESC LIMIT 50"""
            ).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            for key in ("budget_plan_json","checkpoint_json","project_state_json","failure_state_json"):
                try:item[key.removesuffix("_json")]=json.loads(item.pop(key) or "{}")
                except (TypeError,json.JSONDecodeError):item[key.removesuffix("_json")]={}
            result.append(item)
        return result

    def _git_commit(self) -> str:
        if self.repo_root is None:
            return "source-package-no-git-metadata"
        try:
            return subprocess.check_output(
                ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        except Exception:
            return "source-package-no-git-metadata"

    def _production_version(self) -> str:
        if self.repo_root is not None:
            init = self.repo_root / "agent-core" / "src" / "maid_agent" / "__init__.py"
            if init.exists():
                for line in init.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("__version__"):
                        return line.split("=", 1)[1].strip().strip("\"'")
        return "unknown"

    @staticmethod
    def _instructions(cycle: RndCycle) -> str:
        return (
            f"# R&D Agent Input — {cycle.cycle_id}\n\n"
            f"分析游戏日 {cycle.period_start_day} 至 {cycle.period_end_day}。\n\n"
            "硬边界：\n"
            "- 只能修改隔离 worktree。\n"
            "- 禁止覆盖运行中的源码、数据库、世界或 mods。\n"
            "- 先读取本周期经历，自主选择方向、判断价值和规模，再锁定预算，之后才能开发。\n"
            "- 失败、能力缺口和历史项目都是背景资料，不是强制方向。\n"
            "- 不得把任何具体成果当作示范或隐性推荐。\n"
            "- 100,000,000 Token 是上限，不是必须用完；正式周期至少保留 15,000,000 用于构建、修正和收尾。\n"
            "- 达到 50%/75%/85%/95% 时分别重评方向、收缩到成果、停止新增大型范围、强制收尾。\n"
            "- 大型或超出单周期的项目只完成清晰阶段，并保存下一次可继续的位置。\n"
            "- 新周期必须重新比较继续、暂停、放弃、恢复或选择新方向，不能只因既有投入而继续。\n"
            "- 改代码后只运行与本轮成果直接相关的最小检查。\n"
            "- 可复用行为的候选版本不得直接进入生产。\n"
            "- 外部项目只形成交接资料，不得自动安装。\n"
        )
