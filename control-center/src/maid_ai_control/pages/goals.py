from __future__ import annotations
from PySide6.QtWidgets import QHBoxLayout,QLineEdit,QSpinBox,QPushButton,QLabel,QFormLayout,QGroupBox
from maid_ai_control.widgets import Page,DataTable,JsonViewer

class GoalsPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("目标与计划","可提交自然语言目标；Runtime 会保持 Goal / Plan / Step / Action / Postcondition 分层并进行真实验证。",parent);self.api=api
        box=QGroupBox("创建用户目标");form=QFormLayout(box);self.objective=QLineEdit();self.objective.setPlaceholderText("例如：建立一个安全、可恢复施工的小型住所");self.priority=QSpinBox();self.priority.setRange(0,100);self.priority.setValue(70);create=QPushButton("提交目标");create.clicked.connect(self.create_goal);form.addRow("目标",self.objective);form.addRow("优先级",self.priority);form.addRow("",create);self.layout.addWidget(box)
        self.goal=JsonViewer();self.goal.setMaximumHeight(210);self.layout.addWidget(self.goal);self.steps=DataTable([("description","步骤"),("tool","工具"),("status","状态"),("retry_count","重试"),("last_error_code","最后错误"),("request_id","请求 ID")]);self.layout.addWidget(self.steps,1);self.refreshRequested.connect(lambda:self.api.command("GET_STATUS"))
    def create_goal(self):
        text=self.objective.text().strip()
        if not text:self.error("缺少目标","请输入目标。")
        else:self.api.command("CREATE_GOAL",{"objective":text,"priority":self.priority.value()})
    def update_status(self,data):self.goal.set_data(data.get("goal"));self.steps.set_rows(((data.get("plan") or {}).get("steps") or []))
