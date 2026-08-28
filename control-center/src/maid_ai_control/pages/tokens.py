from __future__ import annotations
from PySide6.QtWidgets import QGridLayout,QTabWidget
from maid_ai_control.widgets import Page,Card,DataTable,JsonViewer

class TokensPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("Token 与模型调用","Runtime 和 R&D 使用独立账本；R&D 预算只统计当前五日周期。",parent);self.api=api;grid=QGridLayout();self.cards={}
        for i,(k,t) in enumerate([("runtime_today","Runtime 今日"),("runtime_last_hour","Runtime 近一小时"),("runtime_total","Runtime 总计"),("rnd_used_current_cycle","R&D 当前周期"),("rnd_remaining_current_cycle","R&D 剩余"),("rnd_cycle_id","R&D 周期")]):self.cards[k]=Card(t);grid.addWidget(self.cards[k],i//3,i%3)
        self.layout.addLayout(grid);self.telemetry=DataTable([("created_at","时间"),("ledger","账本"),("purpose","用途"),("model","模型"),("ok","成功"),("http_status","HTTP"),("latency_ms","耗时 ms"),("total_tokens","Token"),("estimated","估算"),("error_code","错误")]);self.layout.addWidget(self.telemetry,1);self.refreshRequested.connect(self.refresh)
    def refresh(self):self.api.command("GET_TOKENS");self.api.command("GET_LLM_TELEMETRY",{"limit":200})
    def update_tokens(self,data):
        for k,c in self.cards.items():c.set_value(data.get(k))
    def update_telemetry(self,data):self.telemetry.set_rows(data.get("requests") or [])
