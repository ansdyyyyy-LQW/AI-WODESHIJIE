from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton

from maid_ai_control.widgets import Card, JsonViewer, Page


class DashboardPage(Page):
    def __init__(self, api, parent=None):
        super().__init__("总览", "查看 EntityMaid 的真实连接、身体状态、当前目标与执行动作。", parent)
        self.api = api
        grid = QGridLayout()
        self.cards = {}
        for index, (key, title) in enumerate(
            [
                ("bridge", "Bridge"),
                ("mode", "Runtime"),
                ("maid", "已绑定女仆"),
                ("day", "游戏日"),
                ("health", "生命值"),
                ("threat", "威胁等级"),
            ]
        ):
            card = Card(title)
            self.cards[key] = card
            grid.addWidget(card, index // 3, index % 3)
        self.layout.addLayout(grid)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("开始")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(lambda: self.api.command("START"))
        buttons.addWidget(self.start_button)
        for text, command in [("暂停", "PAUSE"), ("继续", "RESUME"), ("停止", "STOP"), ("立即战略复评", "REQUEST_REVIEW")]:
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, c=command: self.api.command(c))
            buttons.addWidget(button)
        buttons.addStretch()
        self.layout.addLayout(buttons)
        self.gate_reason = QLabel("等待 Agent Control 连接")
        self.gate_reason.setWordWrap(True)
        self.gate_reason.setStyleSheet("color:#b45309")
        self.layout.addWidget(self.gate_reason)
        self.detail = JsonViewer()
        self.layout.addWidget(self.detail, 1)
        self.refreshRequested.connect(lambda: self.api.command("GET_STATUS"))

    def set_control_connected(self, connected: bool) -> None:
        if not connected:
            self.start_button.setEnabled(False)
            self.gate_reason.setText("等待 Agent Control 连接")

    def update_status(self, data: dict) -> None:
        snapshot = data.get("snapshot") or {}
        self.cards["bridge"].set_value("已连接" if data.get("bridge_connected") else "未连接")
        self.cards["mode"].set_value(data.get("mode"))
        self.cards["maid"].set_value("已绑定" if data.get("bound_maid_uuid") else "未绑定")
        self.cards["day"].set_value(data.get("game_day"))
        self.cards["health"].set_value(f"{data.get('health','—')} / {data.get('max_health','—')}")
        self.cards["threat"].set_value((data.get("threat") or {}).get("risk_level"))
        gate = dict(data.get("start_gate") or {})
        missing = list(gate.get("missing") or [])
        ready = gate.get("ready") is True
        self.start_button.setEnabled(ready)
        self.gate_reason.setText("启动条件已满足" if ready else "暂不能开始：" + "；".join(missing or ["状态尚未完成检查"]))
        self.gate_reason.setStyleSheet("color:#15803d" if ready else "color:#b45309")
        self.detail.set_data(
            {
                "start_gate": gate,
                "strategy": data.get("strategy"),
                "goal": data.get("goal"),
                "plan": data.get("plan"),
                "current_action": data.get("current_action"),
                "reflections": data.get("reflections"),
                "nearby_threats": data.get("nearby_threats"),
                "snapshot": snapshot,
            }
        )
