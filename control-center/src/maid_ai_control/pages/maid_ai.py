from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from maid_ai_control.pages.control import ACTION_TEXT, action_text
from maid_ai_control.user_text import public_model_text
from maid_ai_control.widgets import DataTable, Page


STATUS_MARK = {
    "DONE": "✓",
    "SKIPPED": "✓",
    "RUNNING": "→",
    "PENDING": "○",
    "PAUSED": "○",
    "NEEDS_REVALIDATION": "○",
    "FAILED": "!",
    "BLOCKED": "!",
    "PREEMPTED": "○",
    "CANCELLED": "○",
    "ABORTED": "!",
}


KIND_TEXT = {
    "IF": "如果条件满足",
    "BRANCH": "按当前情况选择",
    "REPEAT": "重复执行",
    "WHILE": "条件满足时继续",
    "UNTIL": "执行到条件满足",
    "ABORT": "安全结束任务",
    "PAUSE": "暂停任务",
}


EVENT_TEXT = {
    "GOAL_CREATED": "建立了新目标",
    "GOAL_STATUS": "目标状态发生变化",
    "PLAN_STATUS": "任务计划有了进展",
    "ACTION_RESULT": "完成了一次操作",
    "ACTION_FAILED": "一次操作没有成功",
    "THREAT_CHANGED": "附近危险情况发生变化",
    "BRIDGE_EVENT": "Minecraft 世界发生变化",
    "DECISION": "AI 作出了新的决定",
    "RUNTIME_ERROR": "AI 遇到问题并已停到安全状态",
    "CAPABILITY_GAP": "AI 记录了一项暂时做不到的能力",
    "RND_STATUS": "AI 研发有了新进展",
}


def _short_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("summary", "decision_summary", "objective", "message", "reason", "description"):
        value = payload.get(key)
        if value:
            return public_model_text(value)
    tool = payload.get("tool") or payload.get("action")
    if tool:
        return ACTION_TEXT.get(str(tool), "执行了一项操作")
    return ""


class MemoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("更多记忆")
        self.resize(940, 640)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.locations = DataTable([("name", "地点"), ("dimension", "所在区域"), ("last_seen_day", "最近发现")])
        self.resources = DataTable([("block_id", "资源"), ("estimated_count", "估计数量"), ("distance", "距离")])
        self.structures = DataTable([("name", "名称"), ("kind", "类型"), ("state", "状态")])
        self.events = DataTable([("game_day", "游戏日"), ("display_type", "事情"), ("display_detail", "说明")])
        tabs.addTab(self.locations, "地点")
        tabs.addTab(self.resources, "资源")
        tabs.addTab(self.structures, "设施")
        tabs.addTab(self.events, "事件")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_data(self, data: dict[str, Any]) -> None:
        self.locations.set_rows(list(data.get("locations") or []))
        self.resources.set_rows(list(data.get("resources") or []))
        self.structures.set_rows(list(data.get("structures") or []))
        rows = []
        for event in list(data.get("events") or []):
            row = dict(event)
            row["display_type"] = EVENT_TEXT.get(str(event.get("type") or ""), "记录了一件事")
            row["display_detail"] = _short_payload(event.get("payload"))
            rows.append(row)
        self.events.set_rows(rows)


class MaidAiPage(Page):
    def __init__(self, api, parent=None):
        super().__init__("女仆AI", "查看 AI 为什么这样做、现在做到哪一步，以及它记住的重要事情。", parent)
        self.api = api
        self._memory: dict[str, Any] = {}
        self.memory_dialog: MemoryDialog | None = None

        strategy_box = QGroupBox("目标与决定")
        form = QFormLayout(strategy_box)
        self.goal = QLabel("正在观察世界")
        self.goal.setWordWrap(True)
        self.decision = QLabel("正在等待第一次决定")
        self.decision.setWordWrap(True)
        self.long_term = QLabel("尚未形成")
        self.stage = QLabel("正在准备")
        self.focus = QLabel("观察当前情况")
        for label, widget in (
            ("当前目标", self.goal),
            ("为什么这样做", self.decision),
            ("长期方向", self.long_term),
            ("当前阶段", self.stage),
            ("当前重点", self.focus),
        ):
            form.addRow(label, widget)
        self.layout.addWidget(strategy_box)

        self.tasks = QTreeWidget()
        self.tasks.setHeaderLabels(["任务清单", "状态"])
        self.tasks.header().setStretchLastSection(False)
        self.tasks.header().resizeSection(0, 760)
        self.layout.addWidget(self.tasks, 2)

        resume_box = QGroupBox("临时应对与继续")
        resume_form = QFormLayout(resume_box)
        self.temporary = QLabel("没有临时应对")
        self.temporary.setWordWrap(True)
        self.world_change = QLabel("尚无新的世界变化")
        self.world_change.setWordWrap(True)
        self.continue_state = QLabel("等待任务")
        self.continue_state.setWordWrap(True)
        resume_form.addRow("当前应对", self.temporary)
        resume_form.addRow("世界变化", self.world_change)
        resume_form.addRow("原任务", self.continue_state)
        self.layout.addWidget(resume_box)

        lower = QHBoxLayout()
        memory_box = QGroupBox("重要记忆")
        memory_layout = QVBoxLayout(memory_box)
        self.memory_summary = QLabel("还没有形成长期记忆")
        self.memory_summary.setWordWrap(True)
        more = QPushButton("查看更多记忆")
        more.clicked.connect(self.show_memory)
        memory_layout.addWidget(self.memory_summary)
        memory_layout.addWidget(more)
        lower.addWidget(memory_box, 1)
        events_box = QGroupBox("最近发生")
        event_layout = QVBoxLayout(events_box)
        self.recent_events = QListWidget()
        event_layout.addWidget(self.recent_events)
        lower.addWidget(events_box, 1)
        self.layout.addLayout(lower, 1)

        self.technical = QGroupBox("展开技术细节")
        self.technical.setCheckable(True)
        self.technical.setChecked(False)
        tech_layout = QVBoxLayout(self.technical)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        tech_layout.addWidget(self.raw)
        self.technical.toggled.connect(self.raw.setVisible)
        self.raw.hide()
        self.layout.addWidget(self.technical)
        self.refreshRequested.connect(self.refresh)

    def refresh(self) -> None:
        self.api.command("GET_STATUS")
        self.api.command("GET_MEMORY", {"event_limit": 80})
        self.api.command("GET_THREAT")
        self.api.command("GET_BUILDING")
        self.api.command("GET_SKILLS")

    def _add_step(self, parent: QTreeWidgetItem | None, step: dict[str, Any]) -> None:
        status = str(step.get("status") or "PENDING")
        kind = str(step.get("kind") or "ACTION")
        text = public_model_text(
            step.get("description"), KIND_TEXT.get(kind) or action_text(step.get("tool")), max_length=220
        )
        item = QTreeWidgetItem([f"{STATUS_MARK.get(status, '○')} {text}", {"DONE": "已完成", "RUNNING": "进行中", "PENDING": "待处理", "SKIPPED": "已跳过", "FAILED": "未完成", "BLOCKED": "暂时受阻", "PAUSED": "已暂停", "PREEMPTED": "已让位", "CANCELLED": "已取消", "ABORTED": "已结束"}.get(status, "待确认")])
        if parent is None:
            self.tasks.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in list(step.get("then_steps") or []):
            self._add_step(item, child)
        for child in list(step.get("else_steps") or []):
            self._add_step(item, child)
        for child in list(step.get("body") or []):
            self._add_step(item, child)
        for branch in list(step.get("branches") or []):
            branch_item = QTreeWidgetItem(["○ 可选路线", "按情况选择"])
            item.addChild(branch_item)
            for child in list(branch.get("steps") or []):
                self._add_step(branch_item, child)

    def update_status(self, data: dict[str, Any]) -> None:
        goal = dict(data.get("goal") or {})
        strategy = dict(data.get("strategy") or {})
        plan = dict(data.get("plan") or {})
        self.goal.setText(public_model_text(goal.get("objective"), "正在观察世界并选择下一步"))
        self.decision.setText(public_model_text(strategy.get("decision_summary"), "正在收集足够信息"))
        self.long_term.setText(public_model_text(strategy.get("long_term_direction"), "稳定生存并逐步发展"))
        self.stage.setText(public_model_text(strategy.get("mid_term_stage") or strategy.get("current_stage"), "当前世界适应阶段"))
        self.focus.setText(public_model_text(strategy.get("current_focus") or goal.get("objective"), "观察当前情况"))
        self.tasks.clear()
        for step in list(plan.get("steps") or []):
            self._add_step(None, step)
        self.tasks.expandAll()
        current = dict(data.get("current_action") or {})
        if current:
            self.temporary.setText(action_text(current.get("tool")))
        elif str(plan.get("status")) == "PAUSED":
            self.temporary.setText("当前任务已暂停，现场进度已保存")
        else:
            self.temporary.setText("没有需要优先处理的临时情况")
        checkpoint = dict(plan.get("checkpoint") or {})
        previous = checkpoint.get("previous_data") or checkpoint.get("last_result") or {}
        self.world_change.setText(_short_payload(previous) or ("附近危险已变化" if data.get("nearby_threats") else "没有需要特别说明的新变化"))
        plan_status = str(plan.get("status") or "PENDING")
        self.continue_state.setText({"RUNNING": "原任务正在继续", "PAUSED": "原任务已保存，可从当前进度继续", "PREEMPTED": "临时情况结束后会重新确认并继续", "DONE": "原任务已经完成", "BLOCKED": "原任务暂时受阻，AI 会重新判断", "TIMEOUT": "原任务已超时，AI 会重新规划", "ABORTED": "原任务已安全结束"}.get(plan_status, "等待下一项任务"))
        self.raw.setPlainText(json.dumps({"strategy": strategy, "goal": goal, "plan": plan, "current_action": current, "snapshot": data.get("snapshot"), "last_error": data.get("last_error")}, ensure_ascii=False, indent=2, default=str))

    def update_memory(self, data: dict[str, Any]) -> None:
        self._memory = data
        summary = dict(data.get("summary") or {})
        parts: list[str] = []
        labels = (("locations", "处地点"), ("resources", "项资源"), ("structures", "处设施"), ("events", "条经历"), ("capability_gaps", "项待提升能力"))
        for key, suffix in labels:
            value = summary.get(key)
            if isinstance(value, int) and value:
                parts.append(f"{value} {suffix}")
        details: list[str] = []
        locations = [str(row.get("name") or "").strip() for row in list(data.get("locations") or [])[:2] if isinstance(row, dict)]
        if any(locations): details.append("最近想到的地点：" + "、".join(x for x in locations if x))
        resources = [str(row.get("block_id") or "").replace("minecraft:", "").replace("_", " ") for row in list(data.get("resources") or [])[:3] if isinstance(row, dict)]
        if any(resources): details.append("记得的资源：" + "、".join(x for x in resources if x))
        structures = [str(row.get("name") or row.get("kind") or "").strip() for row in list(data.get("structures") or [])[:2] if isinstance(row, dict)]
        if any(structures): details.append("重要设施：" + "、".join(x for x in structures if x))
        danger_count = sum(1 for row in list(data.get("events") or [])[:40] if isinstance(row, dict) and str(row.get("type") or "") in {"DAMAGE_TAKEN", "HOSTILE_CONTACT", "HOSTILE_WAVE_DETECTED", "MAID_DEATH", "BASE_DAMAGED"})
        if danger_count: details.append(f"最近记得 {danger_count} 次危险经历")
        heading = "已记住：" + "、".join(parts) if parts else "正在积累重要地点、资源和经历"
        self.memory_summary.setText(heading + ("\n" + "；".join(details) if details else ""))
        self.recent_events.clear()
        for event in list(data.get("events") or [])[:12]:
            title = EVENT_TEXT.get(str(event.get("type") or ""), "记录了一件事")
            detail = _short_payload(event.get("payload"))
            self.recent_events.addItem(f"第 {event.get('game_day', '—')} 天 · {title}" + (f"：{detail}" if detail else ""))
        if self.memory_dialog:
            self.memory_dialog.update_data(data)

    def update_threat(self, data: dict[str, Any]) -> None:
        if str(data.get("risk_level") or "").upper() in {"HIGH", "CRITICAL"}:
            self.recent_events.insertItem(0, "当前 · 附近危险较高，AI 会优先保护女仆")

    def update_building(self, data: dict[str, Any]) -> None:
        _ = data

    def update_skills(self, data: dict[str, Any]) -> None:
        _ = data

    def show_memory(self) -> None:
        if self.memory_dialog is None:
            self.memory_dialog = MemoryDialog(self)
            self.memory_dialog.finished.connect(lambda _result: setattr(self, "memory_dialog", None))
        self.memory_dialog.update_data(self._memory)
        self.memory_dialog.show()
        self.memory_dialog.raise_()
