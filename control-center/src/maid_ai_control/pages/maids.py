from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton

from maid_ai_control.widgets import DataTable, Page


class MaidBindingPage(Page):
    def __init__(self, api, config, parent=None):
        super().__init__("女仆绑定", "先按玩家名称选择当前在线主人，再扫描并绑定该玩家真实拥有的 EntityMaid。", parent)
        self.api = api
        self.config = config
        self.players: list[dict] = []
        self.bound = QLabel("当前女仆：未绑定")
        self.owner = QLabel(f"当前玩家：{config.data.get('selected_owner_name') or '未选择'}")
        self.layout.addWidget(self.owner)
        self.layout.addWidget(self.bound)
        player_row = QHBoxLayout()
        self.player_choice = QComboBox()
        self.player_choice.setMinimumWidth(240)
        scan_players = QPushButton("扫描在线玩家")
        scan_players.clicked.connect(lambda: self.api.command("LIST_PLAYERS"))
        choose = QPushButton("选择玩家")
        choose.clicked.connect(self.select_player)
        player_row.addWidget(self.player_choice)
        player_row.addWidget(scan_players)
        player_row.addWidget(choose)
        player_row.addStretch()
        self.layout.addLayout(player_row)
        buttons = QHBoxLayout()
        discover = QPushButton("扫描该玩家的女仆")
        discover.clicked.connect(lambda: self.api.command("DISCOVER_MAIDS"))
        bind = QPushButton("绑定选中女仆")
        bind.clicked.connect(self.bind_selected)
        unbind = QPushButton("解除绑定")
        unbind.clicked.connect(lambda: self.api.command("UNBIND_MAID"))
        buttons.addWidget(discover)
        buttons.addWidget(bind)
        buttons.addWidget(unbind)
        buttons.addStretch()
        self.layout.addLayout(buttons)
        self.table = DataTable(
            [
                ("name", "名称"),
                ("owner_name", "主人"),
                ("dimension", "维度"),
                ("distance_to_owner", "与主人距离"),
                ("health", "生命值"),
            ]
        )
        self.layout.addWidget(self.table, 1)
        self.refreshRequested.connect(lambda: self.api.command("LIST_PLAYERS"))

    def set_players(self, data: dict) -> None:
        self.players = list(data.get("players") or [])
        self.player_choice.clear()
        for player in self.players:
            self.player_choice.addItem(str(player.get("name") or "未命名玩家"), str(player.get("uuid") or ""))
        selected = str(data.get("selected_owner_uuid") or self.config.data.get("owner_uuid") or "")
        matched=False
        for index, player in enumerate(self.players):
            if str(player.get("uuid") or "") == selected:
                self.player_choice.setCurrentIndex(index)
                matched=True
                break
        if len(self.players) == 1:
            self.player_choice.setCurrentIndex(0)
            self.select_player()
        elif matched:
            self.select_player()
        elif not self.players:
            self.owner.setText("当前玩家：没有在线玩家")

    def select_player(self) -> None:
        index = self.player_choice.currentIndex()
        if index < 0 or index >= len(self.players):
            self.error("无法选择", "当前没有可选择的在线玩家。")
            return
        player = self.players[index]
        player_uuid = str(player.get("uuid") or "")
        player_name = str(player.get("name") or "")
        self.config.data["owner_uuid"] = player_uuid
        self.config.data["selected_owner_name"] = player_name
        self.config.save()
        self.owner.setText(f"当前玩家：{player_name}")
        self.api.command("SELECT_OWNER", {"uuid": player_uuid})

    def owner_selected(self, data: dict) -> None:
        name = str(data.get("selected_owner_name") or self.config.data.get("selected_owner_name") or "")
        if name:
            self.owner.setText(f"当前玩家：{name}")
        self.api.command("DISCOVER_MAIDS")

    def bind_selected(self) -> None:
        if not str(self.config.data.get("owner_uuid") or ""):
            self.error("无法绑定", "请先按名称选择当前在线玩家。")
            return
        row = self.table.selected_data()
        if not row:
            self.error("无法绑定", "请先选择一名女仆。")
        else:
            self.api.command("BIND_MAID", {"maid_uuid": row.get("uuid")})

    def set_maids(self, data: dict) -> None:
        self.table.set_rows(data.get("maids") or [])

    def set_bound(self, uuid: str | None) -> None:
        self.bound.setText(f"当前女仆：{'已绑定' if uuid else '未绑定'}")
