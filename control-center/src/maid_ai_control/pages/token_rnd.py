from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from maid_ai_control.widgets import Card, Page
from maid_ai_control.user_text import public_model_text


PHASE_TEXT = {
    "DECIDING_DIRECTION": "正在决定研发方向",
    "RESEARCHING": "正在查资料",
    "DESIGNING": "正在设计",
    "DEVELOPING": "正在制作",
    "TESTING": "正在测试",
    "FIXING": "正在修正",
    "FINALIZING": "正在整理成果",
    "RESEARCH": "正在查资料",
    "DESIGN": "正在设计",
    "DEVELOPMENT": "正在制作",
    "BUILD_FIX": "正在构建和修正",
    "FINALIZE": "正在整理最终成果",
}


OUTCOME_TEXT = {
    "COMPLETED": "成功完成",
    "STAGE_COMPLETED": "本阶段已完成，可在以后继续",
    "FAILED": "这次没有完成，原因和继续点已经保存",
    "WAITING_USER": "需要你查看后再决定",
}


def _millions(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f} M"
    return f"{number:,}"


class TokenRndPage(Page):
    def __init__(self, api, config, parent=None):
        super().__init__("Token 与 AI研发", "查看日常 AI 用量、五日研发周期和最终成果。", parent)
        self.api = api
        self.config = config
        self.current_day = 0
        self.latest_cycle: dict[str, Any] = {}

        daily = QGroupBox("日常 AI")
        daily_grid = QGridLayout(daily)
        self.daily_cards: dict[str, Card] = {}
        for index, (key, title) in enumerate(
            [
                ("runtime_today", "今天已用"),
                ("runtime_current_stage", "当前五日阶段"),
                ("runtime_total", "累计使用"),
                ("runtime_limit", "每日上限"),
            ]
        ):
            card = Card(title)
            self.daily_cards[key] = card
            daily_grid.addWidget(card, 0, index)
        self.layout.addWidget(daily)

        cycle = QGroupBox("五日研发周期")
        cycle_grid = QGridLayout(cycle)
        self.rnd_cards: dict[str, Card] = {}
        for index, (key, title) in enumerate(
            [
                ("next_day", "下次研发日"),
                ("days_left", "还剩几天"),
                ("budget", "本周期预算"),
                ("used", "已经使用"),
                ("remaining", "还可使用"),
                ("phase", "当前阶段"),
                ("cycle_day", "当前游戏日"),
                ("feasible", "本轮能否完成"),
                ("staging", "是否需要分阶段"),
                ("harness", "研发环境"),
                ("validator", "最终验证"),
            ]
        ):
            card = Card(title)
            self.rnd_cards[key] = card
            cycle_grid.addWidget(card, index // 3, index % 3)
        self.layout.addWidget(cycle)

        result_box = QGroupBox("最近研发成果")
        result_form = QFormLayout(result_box)
        self.result_name = QLabel("还没有研发成果")
        self.result_name.setWordWrap(True)
        self.result_use = QLabel("—")
        self.result_use.setWordWrap(True)
        self.result_state = QLabel("—")
        self.result_state.setWordWrap(True)
        self.user_action = QLabel("暂时不需要操作")
        self.user_action.setWordWrap(True)
        self.artifact_state = QLabel("—")
        self.artifact_state.setWordWrap(True)
        result_form.addRow("成果名称", self.result_name)
        result_form.addRow("用途", self.result_use)
        result_form.addRow("结果", self.result_state)
        result_form.addRow("交付文件", self.artifact_state)
        result_form.addRow("你需要做什么", self.user_action)
        result_buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("打开成果文件夹")
        self.open_folder_button.clicked.connect(self.open_artifact_folder)
        self.readme_button = QPushButton("查看说明")
        self.readme_button.clicked.connect(self.open_readme)
        self.test_button = QPushButton("查看测试结果")
        self.test_button.clicked.connect(self.open_test_result)
        self.handled_button = QPushButton("标记为已处理")
        self.handled_button.clicked.connect(self.mark_handled)
        for button in (self.open_folder_button, self.readme_button, self.test_button, self.handled_button):
            result_buttons.addWidget(button)
        result_buttons.addStretch()
        result_form.addRow(result_buttons)
        self.layout.addWidget(result_box)

        actions = QHBoxLayout()
        self.queries = QLineEdit()
        self.queries.setPlaceholderText("需要查找的 Mod 资料，可输入多个关键词")
        research = QPushButton("查找网络资料")
        research.clicked.connect(self.research)
        actions.addWidget(self.queries, 1)
        actions.addWidget(research)
        self.layout.addLayout(actions)
        self.research_results = QListWidget()
        self.research_results.setMaximumHeight(110)
        self.research_results.hide()
        self.layout.addWidget(self.research_results)

        self.advanced = QGroupBox("展开高级信息")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced)
        self.ids = QLabel("周期编号：—")
        self.ids.setWordWrap(True)
        advanced_layout.addWidget(self.ids)
        self.ids.hide()
        self.advanced.toggled.connect(self.ids.setVisible)
        self.layout.addWidget(self.advanced)
        self.layout.addStretch(1)
        self.refreshRequested.connect(self.refresh)

    def refresh(self) -> None:
        self.api.command("GET_TOKENS")
        self.api.command("GET_RND")

    def update_status(self, data: dict[str, Any]) -> None:
        value = data.get("game_day")
        self.current_day = int(value) if isinstance(value, int) else self.current_day
        self.rnd_cards["cycle_day"].set_value(f"第 {self.current_day} 天" if self.current_day else "—")
        self._update_schedule()

    def _update_schedule(self) -> None:
        days = max(1, int((self.config.data.get("rnd_budget") or {}).get("cycle_game_days", 5)))
        if self.current_day <= 0:
            next_day = days
        elif self.current_day % days == 0:
            next_day = self.current_day + days
        else:
            next_day = ((self.current_day // days) + 1) * days
        self.rnd_cards["next_day"].set_value(f"第 {next_day} 天")
        self.rnd_cards["days_left"].set_value(f"{max(0, next_day - self.current_day)} 天")

    def update_tokens(self, data: dict[str, Any]) -> None:
        for key in ("runtime_today", "runtime_current_stage", "runtime_total"):
            self.daily_cards[key].set_value(_millions(data.get(key)))
        limit = (self.config.data.get("runtime_budget") or {}).get("max_per_game_day")
        self.daily_cards["runtime_limit"].set_value(_millions(limit) if limit else "不限制")
        self.rnd_cards["budget"].set_value(_millions(data.get("rnd_budget_current_cycle", data.get("rnd_budget"))))
        self.rnd_cards["used"].set_value(_millions(data.get("rnd_used_current_cycle", data.get("rnd_used"))))
        self.rnd_cards["remaining"].set_value(_millions(data.get("rnd_remaining_current_cycle", data.get("rnd_remaining"))))

    def update_rnd(self, data: dict[str, Any]) -> None:
        cycles = list(data.get("cycles") or [])
        self.latest_cycle = dict(cycles[0]) if cycles else {}
        readiness = dict(data.get("readiness") or {})
        dsh = dict(readiness.get("deepseek_harness") or {})
        proxy = dict(dsh.get("api_budget_proxy") or {})
        if dsh.get("available"):
            harness_text = f"可用 · {dsh.get('version') or '版本已锁定'}"
            if not proxy.get("available"):
                harness_text += " · 等待研发 API"
        else:
            harness_text = "不可用"
        self.rnd_cards["harness"].set_value(harness_text)
        phase = str(self.latest_cycle.get("dsh_current_phase") or self.latest_cycle.get("phase") or "")
        status = str(self.latest_cycle.get("status") or "")
        outcome = str(self.latest_cycle.get("outcome") or "")
        phase_text = "已暂停，AI 服务启动后会继续" if status == "SUSPENDED" else {"COMPLETED": "完成", "STAGE_COMPLETED": "本阶段完成", "FAILED": "失败", "WAITING_USER": "等待用户操作"}.get(outcome, PHASE_TEXT.get(phase, "等待下一个研发日" if not status else "正在准备"))
        self.rnd_cards["phase"].set_value(phase_text)
        if not self.latest_cycle:
            self.result_name.setText("还没有研发成果")
            self.result_use.setText("—")
            self.result_state.setText("—")
            self.artifact_state.setText("—")
            self.user_action.setText("暂时不需要操作")
            self.rnd_cards["validator"].set_value("等待研发结果")
            for button in (self.open_folder_button, self.readme_button, self.test_button, self.handled_button):
                button.setEnabled(False)
            self.ids.setText("周期编号：—")
            return
        planning = dict(self.latest_cycle.get("project_state") or {})
        budget_plan = dict(self.latest_cycle.get("budget_plan") or {})
        feasible = budget_plan.get("single_cycle_feasible")
        project_size = str(budget_plan.get("project_size") or self.latest_cycle.get("project_size") or "")
        self.rnd_cards["feasible"].set_value("预计可以" if feasible is True else ("预计需要后续周期" if feasible is False else "尚未判断"))
        self.rnd_cards["staging"].set_value("需要" if feasible is False or project_size == "BEYOND_CYCLE" else ("不需要" if feasible is True else "尚未判断"))
        direction = public_model_text(planning.get("direction") or budget_plan.get("direction"), "未命名研发成果")
        value = public_model_text(planning.get("value_reason") or budget_plan.get("value_reason"), "改善女仆 AI 的能力")
        forge_mods = [
            item for item in list(self.latest_cycle.get("artifacts") or [])
            if isinstance(item, dict) and item.get("type") == "forge_mod"
        ]
        self.result_name.setText("新 Mod" if forge_mods else direction)
        self.result_use.setText(value)
        self.result_state.setText(
            "研发成功" if forge_mods and outcome in {"COMPLETED", "STAGE_COMPLETED"}
            else ("已安全暂停，启动后自动继续" if status == "SUSPENDED" else OUTCOME_TEXT.get(outcome, PHASE_TEXT.get(phase, "正在进行")))
        )
        validator = dict(self.latest_cycle.get("final_validator") or {})
        if validator.get("ok") is True:
            validator_text = "通过"
        elif validator.get("ok") is False:
            validator_text = "未通过"
        else:
            validator_text = "等待验证"
        self.rnd_cards["validator"].set_value(validator_text)
        artifact_count = int(self.latest_cycle.get("artifact_count") or 0)
        self.artifact_state.setText(
            f"{len(forge_mods)} 个 Forge Mod" if forge_mods
            else (f"{artifact_count} 个已登记文件" if artifact_count else "尚无已登记文件")
        )
        handled = bool(self.latest_cycle.get("handled"))
        if forge_mods:
            user_action = "需要安装并重启 Minecraft"
        elif outcome == "WAITING_USER":
            user_action = "请查看说明和测试结果，再决定是否继续"
        elif outcome in {"COMPLETED", "STAGE_COMPLETED"} and not handled:
            user_action = "请查看成果；确认后可标记为已处理"
        elif outcome == "FAILED":
            user_action = "无需立刻处理；失败原因和继续点已经保存"
        else:
            user_action = "暂时不需要操作"
        self.user_action.setText("已处理" if handled else user_action)
        artifact = Path(str(self.latest_cycle.get("artifact_dir") or ""))
        self.open_folder_button.setEnabled(bool(str(artifact)))
        self.readme_button.setEnabled(bool(str(artifact)))
        self.test_button.setEnabled(bool(str(artifact)))
        self.handled_button.setEnabled(bool(outcome) and not handled)
        self.ids.setText(
            f"周期编号：{self.latest_cycle.get('cycle_id', '—')}\n"
            f"项目编号：{self.latest_cycle.get('project_id', '—')}\n"
            f"DSH 版本：{self.latest_cycle.get('dsh_version') or '—'}\n"
            f"DSH 会话：{self.latest_cycle.get('dsh_session_id') or '—'}\n"
            f"隔离目录：{self.latest_cycle.get('dsh_workspace') or '—'}\n"
            f"源码基线：{self.latest_cycle.get('baseline_commit') or '—'}"
        )

    def research(self) -> None:
        values = [x.strip() for x in self.queries.text().replace("，", ",").split(",") if x.strip()]
        if not values:
            self.error("缺少关键词", "请输入至少一个需要查找的关键词。")
            return
        self.api.command("RESEARCH_MODS", {"queries": values})

    def update_research(self, data: dict[str, Any]) -> None:
        self.research_results.clear()
        results = list(data.get("results") or [])
        shown = 0
        for group in results:
            if not isinstance(group, dict):
                continue
            query = str(group.get("query") or "当前关注方向")
            requirements = dict(group.get("requirements") or {})
            game_version = str(requirements.get("game_version") or "未确认")
            loader = str(requirements.get("loader") or "").lower()
            for row in list(group.get("modrinth") or []):
                if not isinstance(row, dict):
                    continue
                versions = list(row.get("compatible_versions") or [])
                dependencies_ok = any(bool(version.get("dependencies_compatible")) for version in versions if isinstance(version, dict))
                title = str(row.get("title") or "未命名 Mod")
                compatible = str(row.get("status") or "") == "COMPATIBLE"
                text = (
                    f"{title} · Minecraft {game_version} · "
                    f"Forge：{'适用' if loader == 'forge' and compatible else '未确认'} · "
                    f"依赖：{'齐全' if compatible and dependencies_ok else '需要检查'} · "
                    f"关注原因：与“{query}”有关"
                )
                self.research_results.addItem(text)
                shown += 1
            if not group.get("modrinth") and group.get("errors"):
                self.research_results.addItem(f"“{query}”暂时没有得到可核验结果。")
                shown += 1
        if not shown:
            self.research_results.addItem("没有找到可用资料，查询记录已经保存。")
        self.research_results.show()

    def _artifact(self) -> Path | None:
        raw = str(self.latest_cycle.get("artifact_dir") or "")
        return Path(raw) if raw else None

    @staticmethod
    def _open(path: Path | None) -> None:
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_artifact_folder(self) -> None:
        self._open(self._artifact())

    def open_readme(self) -> None:
        root = self._artifact()
        if not root:
            return
        for name in ("USER_ACTION_REQUIRED.md", "RND_REPORT.md", "output/rnd_result.json", "README.md", "readme.md", "rnd_result.json", "summary.md"):
            candidate = root / name
            if candidate.exists():
                self._open(candidate)
                return
        self._open(root)

    def open_test_result(self) -> None:
        root = self._artifact()
        if not root:
            return
        for name in ("output/final-validator/runner_result.json", "output/final_validator_result.json", "validation/test-results.txt", "test-results", "test_result.json", "build_report.json", "rnd_result.json"):
            candidate = root / name
            if candidate.exists():
                self._open(candidate)
                return
        self._open(root)

    def mark_handled(self) -> None:
        cycle_id = str(self.latest_cycle.get("cycle_id") or "")
        if cycle_id:
            self.api.command("MARK_RND_HANDLED", {"cycle_id": cycle_id})
