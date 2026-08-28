from __future__ import annotations
from PySide6.QtWidgets import QGridLayout
from maid_ai_control.widgets import Page,Card,DataTable

class ThreatPage(Page):
    def __init__(self,api,parent=None):
        super().__init__("威胁分析","按真实昼夜窗口统计敌对接触、伤害、死亡、撤退和主要进入方向，并影响战略复评。",parent);self.api=api;grid=QGridLayout();self.cards={}
        for i,(k,t) in enumerate([("risk_level","风险"),("hostile_contacts","敌对接触"),("damage_taken","累计伤害"),("deaths","死亡"),("dominant_directions","主要方向"),("attacker_types","攻击者")]):self.cards[k]=Card(t);grid.addWidget(self.cards[k],i//3,i%3)
        self.layout.addLayout(grid);self.table=DataTable([("day","日"),("period","时段"),("hostile_contacts","接触"),("unique_hostiles","不同敌人"),("damage_taken","伤害"),("deaths","死亡"),("retreats","撤退"),("base_damage_events","基地受损"),("entry_direction_histogram","方向")]);self.layout.addWidget(self.table,1);self.refreshRequested.connect(lambda:self.api.command("GET_THREAT"))
    def update_data(self,data):
        for k,card in self.cards.items():card.set_value(data.get(k))
        self.table.set_rows(data.get("windows") or [])
