from __future__ import annotations
from PySide6.QtWidgets import QHBoxLayout,QPushButton,QLineEdit,QLabel,QTabWidget
from maid_ai_control.widgets import Page,DataTable,JsonViewer

class RndPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("R&D Harness","每 5 个游戏日使用独立 R&D Token 周期，在隔离源码副本中读取、修改、构建并输出 Handoff；绝不自动修改生产世界或安装 Mod。",parent);self.api=api;self.readiness=JsonViewer();self.readiness.setMaximumHeight(150);self.layout.addWidget(self.readiness)
        row=QHBoxLayout();self.queries=QLineEdit();self.queries.setPlaceholderText("Mod 调研关键词，用逗号分隔");research=QPushButton("调研到 Handoff");research.clicked.connect(self.research);row.addWidget(self.queries,1);row.addWidget(research);self.layout.addLayout(row)
        tabs=QTabWidget();self.cycles=DataTable([("trigger_day","触发日"),("cycle_id","周期"),("status","状态"),("mode","模式"),("token_budget","Token 预算"),("artifact_dir","产物目录"),("summary","摘要")]);self.candidates=DataTable([("name","候选技能"),("version","版本"),("status","状态"),("success_count","成功"),("failure_count","失败"),("source_path","来源")]);tabs.addTab(self.cycles,"研发周期");tabs.addTab(self.candidates,"候选技能");self.layout.addWidget(tabs,1);self.refreshRequested.connect(lambda:self.api.command("GET_RND"))
    def research(self):
        values=[x.strip() for x in self.queries.text().replace("，",",").split(",") if x.strip()]
        if not values:self.error("缺少关键词","请输入至少一个关键词。")
        else:self.api.command("RESEARCH_MODS",{"queries":values})
    def update_data(self,data):self.readiness.set_data(data.get("readiness"));self.cycles.set_rows(data.get("cycles") or []);self.candidates.set_rows(data.get("candidate_skills") or [])
