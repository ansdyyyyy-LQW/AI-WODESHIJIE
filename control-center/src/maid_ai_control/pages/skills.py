from __future__ import annotations
from PySide6.QtWidgets import QHBoxLayout,QPushButton,QTabWidget
from maid_ai_control.widgets import Page,DataTable

class SkillsPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("技能库","Runtime 只会执行 ACTIVE 技能；R&D 产出的 CANDIDATE 必须人工检查后激活。",parent);self.api=api;buttons=QHBoxLayout();activate=QPushButton("激活选中版本");activate.clicked.connect(lambda:self.set_status("ACTIVE"));disable=QPushButton("禁用选中版本");disable.clicked.connect(lambda:self.set_status("DISABLED"));buttons.addWidget(activate);buttons.addWidget(disable);buttons.addStretch();self.layout.addLayout(buttons)
        tabs=QTabWidget();self.table=DataTable([("name","名称"),("version","版本"),("status","状态"),("kind","类型"),("success_count","成功"),("failure_count","失败"),("success_rate","成功率"),("consecutive_failures","连续失败"),("last_failure_code","最后错误")]);self.queue=DataTable([("skill_id","Skill ID"),("version","版本"),("reason","原因"),("status","状态"),("created_at","创建时间")]);tabs.addTab(self.table,"技能");tabs.addTab(self.queue,"待返修");self.layout.addWidget(tabs,1);self.refreshRequested.connect(lambda:self.api.command("GET_SKILLS"))
    def set_status(self,status):
        row=self.table.selected_data()
        if not row:self.error("未选择技能","请先选择一行。")
        else:self.api.command("SET_SKILL_STATUS",{"skill_id":row["skill_id"],"version":row["version"],"status":status})
    def update_data(self,data):self.table.set_rows(data.get("skills") or []);self.queue.set_rows(data.get("refinement_queue") or [])
