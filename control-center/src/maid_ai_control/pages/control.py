from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from maid_ai_control.widgets import Card, DataTable, Page
from maid_ai_control.user_text import public_model_text


ACTION_TEXT = {
    "inspect_entity": "查看目标",
    "inspect_nearby_entities": "观察周围",
    "inspect_block": "查看方块",
    "inspect_local_space": "检查脚下与前方",
    "move_to": "前往目标位置",
    "move_forward": "向前移动",
    "move_backward": "向后移动",
    "strafe_left": "向左移动",
    "strafe_right": "向右移动",
    "approach_entity": "靠近目标",
    "move_away_from_entity": "远离危险",
    "maintain_distance": "保持安全距离",
    "jump": "跳跃",
    "short_sprint": "短距离快跑",
    "attack_entity": "攻击目标",
    "interact_entity": "与目标互动",
    "use_main_hand": "使用主手物品",
    "use_off_hand": "使用副手物品",
    "use_item_on_block": "在方块上使用物品",
    "interact_block": "与方块互动",
    "break_block": "挖掘方块",
    "mine_block": "挖掘方块",
    "place_block": "放置方块",
    "pickup_nearby": "拾取附近物品",
    "equip": "装备物品",
    "select_item": "选择物品",
    "use_item": "使用物品",
    "craft": "制作物品",
    "smelt": "熔炼物品",
    "open_container": "打开容器",
    "take_from_container": "从容器取出物品",
    "put_into_container": "把物品放入容器",
    "transfer_container": "整理容器物品",
    "eat": "进食",
    "retreat_from": "撤离危险目标",
    "hold_position": "保持当前位置",
    "dig_region": "挖掘指定区域",
    "place_region": "铺设指定区域",
    "build_chunk": "继续施工",
    "cancel_action": "停止当前操作",
    "stop": "停止移动",
    "wait": "短暂等待",
    "wait_until": "等待条件满足",
    "build_blueprint": "按蓝图施工",
}


def action_text(tool: str | None, description: str | None = None) -> str:
    mapped = ACTION_TEXT.get(str(tool or ""), "正在处理当前任务")
    return public_model_text(description, mapped, max_length=180) if description else mapped


def _flatten_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in steps:
        rows.append(step)
        rows.extend(_flatten_steps(list(step.get("then_steps") or [])))
        rows.extend(_flatten_steps(list(step.get("else_steps") or [])))
        rows.extend(_flatten_steps(list(step.get("body") or [])))
        for branch in list(step.get("branches") or []):
            rows.extend(_flatten_steps(list(branch.get("steps") or [])))
    return rows


class MaidChooserDialog(QDialog):
    def __init__(self, page: "ControlPage") -> None:
        super().__init__(page)
        self.page = page
        self.setWindowTitle("切换女仆")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        note = QLabel("先选择当前在线玩家，再选择该玩家拥有的女仆。")
        note.setWordWrap(True)
        layout.addWidget(note)
        owner_row = QHBoxLayout()
        self.players = QComboBox()
        self.players.setMinimumWidth(260)
        refresh_players = QPushButton("查找在线玩家")
        refresh_players.clicked.connect(lambda: page.api.command("LIST_PLAYERS"))
        choose_owner = QPushButton("确认玩家")
        choose_owner.clicked.connect(self._choose_owner)
        owner_row.addWidget(self.players, 1)
        owner_row.addWidget(refresh_players)
        owner_row.addWidget(choose_owner)
        layout.addLayout(owner_row)
        self.owner = QLabel("当前玩家：未选择")
        layout.addWidget(self.owner)
        maid_row = QHBoxLayout()
        discover = QPushButton("查找女仆")
        discover.clicked.connect(lambda: page.api.command("DISCOVER_MAIDS"))
        bind = QPushButton("使用选中的女仆")
        bind.clicked.connect(self._bind)
        maid_row.addWidget(discover)
        maid_row.addWidget(bind)
        maid_row.addStretch()
        layout.addLayout(maid_row)
        self.maids = DataTable(
            [
                ("name", "名称"),
                ("owner_name", "主人"),
                ("dimension", "所在区域"),
                ("distance_to_owner", "与主人距离"),
                ("health", "生命值"),
            ]
        )
        layout.addWidget(self.maids, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_players(page.players)
        self.update_maids(page.maids)

    def update_players(self, players: list[dict[str, Any]]) -> None:
        self.players.clear()
        for player in players:
            self.players.addItem(str(player.get("name") or "未命名玩家"), str(player.get("uuid") or ""))
        wanted = str(self.page.config.data.get("owner_uuid") or "")
        for index in range(self.players.count()):
            if str(self.players.itemData(index) or "") == wanted:
                self.players.setCurrentIndex(index)
                break
        name = str(self.page.config.data.get("selected_owner_name") or "")
        self.owner.setText(f"当前玩家：{name or '未选择'}")

    def update_maids(self, maids: list[dict[str, Any]]) -> None:
        self.maids.set_rows(maids)

    def owner_selected(self, data: dict[str, Any]) -> None:
        name = str(data.get("selected_owner_name") or "")
        self.owner.setText(f"当前玩家：{name or '未选择'}")

    def _choose_owner(self) -> None:
        index = self.players.currentIndex()
        if index < 0:
            self.page.error("无法选择", "当前没有在线玩家。")
            return
        player_uuid = str(self.players.itemData(index) or "")
        player_name = self.players.currentText()
        self.page.config.data["owner_uuid"] = player_uuid
        self.page.config.data["selected_owner_name"] = player_name
        self.page.config.save()
        self.page.api.command("SELECT_OWNER", {"uuid": player_uuid})

    def _bind(self) -> None:
        row = self.maids.selected_data()
        if not row:
            self.page.error("无法切换", "请先选择一名女仆。")
            return
        self.page.selected_maid_name = str(row.get("name") or "")
        self.page.api.command("BIND_MAID", {"maid_uuid": row.get("uuid")})


class ControlPage(Page):
    def __init__(self, api, config, parent=None):
        super().__init__("控制", "查看女仆是否就绪，并控制 AI 的开始、暂停和停止。", parent)
        self.api = api
        self.config = config
        self.players: list[dict[str, Any]] = []
        self.maids: list[dict[str, Any]] = []
        self.selected_maid_name = ""
        self.chooser: MaidChooserDialog | None = None

        grid = QGridLayout()
        self.cards: dict[str, Card] = {}
        for index, (key, title) in enumerate(
            [
                ("minecraft", "Minecraft"),
                ("maid", "女仆"),
                ("ai", "AI"),
                ("health", "生命值"),
                ("time", "游戏时间"),
                ("position", "位置"),
                ("danger", "危险情况"),
            ]
        ):
            card = Card(title)
            self.cards[key] = card
            grid.addWidget(card, index // 4, index % 4)
        self.layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("开始 AI")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("继续")
        self.stop_button = QPushButton("停止")
        self.switch_button = QPushButton("切换女仆")
        self.refresh_status_button = QPushButton("刷新状态")
        self.start_button.clicked.connect(lambda: self.api.command("START"))
        self.pause_button.clicked.connect(lambda: self.api.command("PAUSE"))
        self.resume_button.clicked.connect(lambda: self.api.command("RESUME"))
        self.stop_button.clicked.connect(lambda: self.api.command("STOP"))
        self.switch_button.clicked.connect(self.open_chooser)
        self.refresh_status_button.clicked.connect(self.refresh)
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button, self.switch_button, self.refresh_status_button):
            buttons.addWidget(button)
        buttons.addStretch()
        self.layout.addLayout(buttons)

        self.gate_reason = QLabel("正在等待 AI 服务连接。")
        self.gate_reason.setWordWrap(True)
        self.gate_reason.setStyleSheet("color:#b45309")
        self.layout.addWidget(self.gate_reason)

        work = QGroupBox("当前进展")
        work_grid = QGridLayout(work)
        self.now = QLabel("尚未开始")
        self.now.setWordWrap(True)
        self.progress = QLabel("0 / 0")
        self.next_step = QLabel("尚无下一步")
        self.next_step.setWordWrap(True)
        work_grid.addWidget(QLabel("现在："), 0, 0)
        work_grid.addWidget(self.now, 0, 1)
        work_grid.addWidget(QLabel("进度："), 1, 0)
        work_grid.addWidget(self.progress, 1, 1)
        work_grid.addWidget(QLabel("下一步："), 2, 0)
        work_grid.addWidget(self.next_step, 2, 1)
        work_grid.setColumnStretch(1, 1)
        self.layout.addWidget(work)
        self.layout.addStretch(1)
        self.refreshRequested.connect(self.refresh)

    def refresh(self) -> None:
        self.api.command("GET_STATUS")

    def set_control_connected(self, connected: bool) -> None:
        if not connected:
            self.start_button.setEnabled(False)
            self.cards["ai"].set_value("服务未连接")
            self.gate_reason.setText("正在等待 AI 服务连接。")

    def open_chooser(self) -> None:
        if self.chooser is None:
            self.chooser = MaidChooserDialog(self)
            self.chooser.finished.connect(lambda _result: setattr(self, "chooser", None))
        self.api.command("LIST_PLAYERS")
        self.chooser.show()
        self.chooser.raise_()

    def set_players(self, data: dict[str, Any]) -> None:
        self.players = list(data.get("players") or [])
        if self.chooser:
            self.chooser.update_players(self.players)

    def owner_selected(self, data: dict[str, Any]) -> None:
        name = str(data.get("selected_owner_name") or "")
        if name:
            self.config.data["selected_owner_name"] = name
            self.config.save()
        if self.chooser:
            self.chooser.owner_selected(data)
        self.api.command("DISCOVER_MAIDS")

    def set_maids(self, data: dict[str, Any]) -> None:
        self.maids = list(data.get("maids") or [])
        if self.chooser:
            self.chooser.update_maids(self.maids)

    def update_status(self, data: dict[str, Any]) -> None:
        snapshot = dict(data.get("snapshot") or {})
        bound = bool(data.get("bound_maid_uuid"))
        self.cards["minecraft"].set_value("已连接" if data.get("bridge_connected") else "未连接")
        maid_name = str(snapshot.get("maid_name") or self.selected_maid_name or "")
        self.cards["maid"].set_value(maid_name if bound and maid_name else ("已连接" if bound else "未选择"))
        mode = str(data.get("mode") or "STOPPED")
        self.cards["ai"].set_value({"RUNNING": "运行中", "PAUSED": "已暂停", "STOPPED": "已停止", "SAFE_IDLE": "安全等待", "WAITING_SNAPSHOT": "等待游戏状态"}.get(mode, "正在准备"))
        health = snapshot.get("health", data.get("health"))
        max_health = snapshot.get("max_health", data.get("max_health"))
        self.cards["health"].set_value(f"{health:g} / {max_health:g}" if isinstance(health, (int, float)) and isinstance(max_health, (int, float)) else "—")
        day = snapshot.get("day", data.get("game_day"))
        ticks = snapshot.get("time_of_day")
        if isinstance(ticks, (int, float)):
            hour = (int(ticks) // 1000 + 6) % 24
            minute = int((int(ticks) % 1000) * 60 / 1000)
            time_text = f"第 {day} 天  {hour:02d}:{minute:02d}"
        else:
            time_text = f"第 {day} 天" if day is not None else "—"
        self.cards["time"].set_value(time_text)
        pos = dict(snapshot.get("position") or {})
        dimension = str(snapshot.get("dimension") or "").replace("minecraft:", "")
        if all(isinstance(pos.get(k), (int, float)) for k in ("x", "y", "z")):
            self.cards["position"].set_value(f"{dimension or '当前区域'}  {round(pos['x'])}, {round(pos['y'])}, {round(pos['z'])}")
        else:
            self.cards["position"].set_value("—")
        threats = list(data.get("nearby_threats") or [])
        targeting = sum(1 for row in threats if row.get("targeting_maid"))
        if targeting:
            danger = f"{targeting} 个危险目标正在靠近"
        elif threats:
            danger = f"附近有 {len(threats)} 个危险目标"
        else:
            danger = "附近安全"
        self.cards["danger"].set_value(danger)

        gate = dict(data.get("start_gate") or {})
        ready = gate.get("ready") is True
        missing = []
        for value in list(gate.get("missing") or []):
            text = str(value)
            for source, target in (
                ("R&D Harness 或 Source Workspace", "本地研发环境"),
                ("Runtime API", "日常 AI 接口"), ("R&D API", "AI 研发接口"),
                ("Minecraft Bridge", "Minecraft 连接组件"), ("Bridge", "游戏连接"),
                ("EntityMaid", "女仆"), ("Harness", "本地研发程序"),
                ("Source Workspace", "研发源码目录"),
            ):
                text = text.replace(source, target)
            missing.append(text)
        self.start_button.setEnabled(ready)
        self.gate_reason.setText("已经可以开始。" if ready else "暂时不能开始：" + "；".join(missing or ["状态仍在检查中"]))
        self.gate_reason.setStyleSheet("color:#15803d" if ready else "color:#b45309")
        self.pause_button.setEnabled(mode in {"RUNNING", "SAFE_IDLE", "WAITING_SNAPSHOT"})
        self.resume_button.setEnabled(mode == "PAUSED")
        self.stop_button.setEnabled(mode != "STOPPED")

        plan = dict(data.get("plan") or {})
        rows = _flatten_steps(list(plan.get("steps") or []))
        done_states = {"DONE", "SKIPPED"}
        completed = sum(1 for row in rows if str(row.get("status")) in done_states)
        pending = next((row for row in rows if str(row.get("status")) in {"PENDING", "RUNNING"}), None)
        current = dict(data.get("current_action") or {})
        self.now.setText(action_text(current.get("tool"), pending.get("description") if pending and str(pending.get("status")) == "RUNNING" else None) if current or pending else ("任务已完成" if rows and completed == len(rows) else "正在观察世界"))
        self.progress.setText(f"{completed} / {len(rows)}")
        next_pending = next((row for row in rows if str(row.get("status")) == "PENDING" and row is not pending), None)
        self.next_step.setText(action_text(next_pending.get("tool"), next_pending.get("description")) if next_pending else "等待新的决定")
