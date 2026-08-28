from __future__ import annotations
from PySide6.QtWidgets import QTabWidget
from maid_ai_control.widgets import Page,DataTable,Card

class MemoryPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("长期记忆","这些记录会进入后续规划上下文，不只是日志。",parent);self.api=api;self.summary=Card("记忆概况");self.layout.addWidget(self.summary);tabs=QTabWidget();self.events=DataTable([("game_day","日"),("game_tick","Tick"),("type","事件"),("severity","级别"),("payload","内容"),("source","来源")]);self.locations=DataTable([("name","地点"),("dimension","维度"),("x","X"),("y","Y"),("z","Z"),("tags","标签"),("last_seen_day","最近日")]);self.resources=DataTable([("block_id","资源"),("dimension","维度"),("x","X"),("y","Y"),("z","Z"),("estimated_count","估计数量"),("distance","距离")]);self.structures=DataTable([("kind","类型"),("name","名称"),("dimension","维度"),("x","X"),("y","Y"),("z","Z"),("state","状态")]);tabs.addTab(self.events,"事件");tabs.addTab(self.locations,"地点");tabs.addTab(self.resources,"资源");tabs.addTab(self.structures,"结构/工作站");self.layout.addWidget(tabs,1);self.refreshRequested.connect(lambda:self.api.command("GET_MEMORY"))
    def update_data(self,data):self.summary.set_value(data.get("summary"));self.events.set_rows(data.get("events") or []);self.locations.set_rows(data.get("locations") or []);self.resources.set_rows(data.get("resources") or []);self.structures.set_rows(data.get("structures") or [])
