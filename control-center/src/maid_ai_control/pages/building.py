from __future__ import annotations

import json

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox

from maid_ai_control.widgets import DataTable, JsonViewer, Page


EXAMPLE_DSL = {
    "name": "5x5 生存平台",
    "operations": [
        {"op": "floor", "x1": 0, "z1": 0, "x2": 4, "z2": 4, "y": 0, "item": "minecraft:cobblestone"},
        {"op": "outline_rectangle", "x1": 0, "z1": 0, "x2": 4, "z2": 4, "y": 1, "item": "minecraft:oak_planks"},
    ],
}


class BuildingPage(Page):
    def __init__(self, api, parent=None):
        super().__init__("蓝图施工", "建筑原语先展开成 BlueprintBlock，再由唯一 BlueprintExecutor 控制 EntityMaid 真实施工。", parent)
        self.api = api
        controls = QHBoxLayout()
        self.x, self.y, self.z = QSpinBox(), QSpinBox(), QSpinBox()
        for widget in (self.x, self.y, self.z):
            widget.setRange(-30_000_000, 30_000_000)
        self.rotation = QComboBox()
        self.rotation.addItems(["0", "90", "180", "270"])
        execute = QPushButton("执行建筑 DSL")
        execute.clicked.connect(self.execute_dsl)
        for label, widget in (("原点 X", self.x), ("Y", self.y), ("Z", self.z), ("旋转", self.rotation)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(execute)
        controls.addStretch()
        self.layout.addLayout(controls)
        self.dsl = QPlainTextEdit(json.dumps(EXAMPLE_DSL, ensure_ascii=False, indent=2))
        self.dsl.setMaximumHeight(220)
        self.layout.addWidget(self.dsl)
        self.table = DataTable(
            [
                ("build_id", "Build ID"),
                ("blueprint.name", "蓝图"),
                ("status", "状态"),
                ("next_segment", "下一段"),
                ("completed_blocks", "完成方块"),
                ("missing_items", "缺少材料"),
                ("last_error", "最后错误"),
                ("updated_at", "更新时间"),
            ]
        )
        self.detail = JsonViewer()
        self.detail.setMaximumHeight(190)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.layout.addWidget(self.table, 1)
        self.layout.addWidget(self.detail)
        self.refreshRequested.connect(lambda: self.api.command("GET_BUILDING"))

    def execute_dsl(self) -> None:
        try:
            spec = json.loads(self.dsl.toPlainText())
            if not isinstance(spec, dict):
                raise ValueError("DSL 顶层必须是对象")
        except Exception as exc:
            self.error("DSL 无法执行", str(exc))
            return
        self.api.command(
            "EXECUTE_BUILDING_DSL",
            {
                "dsl": spec,
                "origin": {"x": self.x.value(), "y": self.y.value(), "z": self.z.value()},
                "rotation": int(self.rotation.currentText()),
            },
        )

    def show_selected(self) -> None:
        self.detail.set_data(self.table.selected_data())

    def update_data(self, data: dict) -> None:
        self.table.set_rows(data.get("builds") or [])
