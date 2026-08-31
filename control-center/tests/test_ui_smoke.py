from __future__ import annotations

import pytest
pytest.importorskip("PySide6")
from pathlib import Path
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel
from maid_ai_control.app import MainWindow
from maid_ai_control.api_client import ApiClient
from maid_ai_control.config import ConfigManager
from maid_ai_control.pages.dashboard import DashboardPage
from maid_ai_control.pages.maids import MaidBindingPage
from maid_ai_control.pages.memory import MemoryPage
from maid_ai_control.pages.skills import SkillsPage
from maid_ai_control.pages.settings import SettingsPage
from maid_ai_control.pages import ControlPage, MaidAiPage, TokenRndPage
from maid_ai_control.pages.control import action_text
from maid_ai_control.user_text import public_model_text


def test_production_pages_construct(tmp_path:Path):
    app=QApplication.instance() or QApplication([])
    cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data")
    api=ApiClient(cfg.control_url,cfg.data["control_token"])
    pages=[DashboardPage(api),MaidBindingPage(api,cfg),MemoryPage(api),SkillsPage(api),SettingsPage(cfg)]
    assert all(page.layout is not None for page in pages)
    for page in pages:page.deleteLater()
    app.processEvents()


def test_03_four_pages_plain_text_and_button_routes(tmp_path:Path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data")
    monkeypatch.setattr(MainWindow,"_start_runtime",lambda self:None)
    monkeypatch.setattr("maid_ai_control.app.ApiClient.command",lambda self,*args,**kwargs:None)
    monkeypatch.setattr("maid_ai_control.app.ProcessSupervisor.stop_agent",lambda self:None)
    window=MainWindow(cfg)
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())]==["控制","女仆AI","Token 与 AI研发","设置"]
    for index in range(4):
        window.navigation.setCurrentRow(index)
        assert window.stack.currentIndex()==index

    class FakeApi:
        connected=True
        def __init__(self):self.calls=[]
        def command(self,name,args=None):self.calls.append((name,args or {}))
    fake=FakeApi();control=ControlPage(fake,cfg);maid=MaidAiPage(fake);token=TokenRndPage(fake,cfg)
    control.start_button.setEnabled(True);control.start_button.click();control.pause_button.setEnabled(True);control.pause_button.click();control.resume_button.setEnabled(True);control.resume_button.click();control.stop_button.setEnabled(True);control.stop_button.click()
    assert [name for name,_args in fake.calls[:4]]==["START","PAUSE","RESUME","STOP"]
    control.refresh_status_button.click()
    assert fake.calls[-1][0]=="GET_STATUS"
    assert action_text("break_block")=="挖掘方块"
    filtered=public_model_text('{"objective":"继续任务 11111111-1111-4111-8111-111111111111","goal_id":"raw"}')
    assert "{" not in filtered and "11111111" not in filtered and "继续任务" in filtered
    control.update_status({"mode":"RUNNING","bridge_connected":True,"bound_maid_uuid":"maid","game_day":2,"snapshot":{"maid_name":"小女仆","day":2,"time_of_day":6000,"health":18,"max_health":20,"dimension":"minecraft:overworld","position":{"x":1,"y":64,"z":2}},"nearby_threats":[],"plan":{"steps":[]},"start_gate":{"ready":True,"missing":[]}})
    assert control.cards["maid"].value.text()=="小女仆" and "第 2 天" in control.cards["time"].value.text()
    token.update_rnd({
        "readiness":{"deepseek_harness":{"available":True,"version":"0.1.1-rc.2","api_budget_proxy":{"available":True}}},
        "cycles":[{
            "cycle_id":"cycle-001","status":"COMPLETED","outcome":"COMPLETED",
            "dsh_current_phase":"FINALIZE","dsh_version":"0.1.1-rc.2",
            "dsh_session_id":"session-1","dsh_workspace":str(tmp_path/"workspace"),
            "baseline_commit":"abc123","artifact_dir":str(tmp_path/"handoff"),
            "artifact_count":2,"final_validator":{"ok":True},
        }],
    })
    assert "0.1.1-rc.2" in token.rnd_cards["harness"].value.text()
    assert token.rnd_cards["validator"].value.text()=="通过"
    assert token.artifact_state.text()=="2 个已登记文件"
    token.update_rnd({
        "readiness":{"deepseek_harness":{"available":True,"version":"0.1.1-rc.2","api_budget_proxy":{"available":True}}},
        "cycles":[{
            "cycle_id":"cycle-002","status":"COMPLETED","outcome":"COMPLETED",
            "artifact_dir":str(tmp_path/"forge-handoff"),"artifact_count":3,
            "final_validator":{"ok":True},
            "artifacts":[{"type":"forge_mod","name":"Example Mod","mod_id":"examplemod","sha256":"hidden"}],
        }],
    })
    assert token.result_name.text()=="新 Mod"
    assert token.result_state.text()=="研发成功"
    assert token.user_action.text()=="需要安装并重启 Minecraft"
    assert token.artifact_state.text()=="1 个 Forge Mod"
    assert token.open_folder_button.isEnabled() and token.readme_button.isEnabled()
    assert "examplemod" not in token.result_name.text() and "hidden" not in token.result_name.text()
    window.settings_page.update_harness_readiness({"readiness":{"deepseek_harness":{"available":True,"startup":True,"version":"0.1.1-rc.2"}}})
    assert window.settings_page.harness_status.text()=="启动检查通过"
    assert window.settings_page.harness_version.text()=="0.1.1-rc.2"

    forbidden=("Bridge","Runtime","Goal","Plan","PlanStep","Action","ThreatAnalytics","Token Ledger","R&D Harness","Start Gate","Postcondition","Checkpoint","UUID","JSON","Worktree","Runner","Patch","Candidate")
    skipped={maid.technical,token.advanced,window.settings_page.advanced}
    for page in (control,maid,token,window.settings_page):
        for widget in page.findChildren(QLabel)+page.findChildren(QAbstractButton):
            parent=widget.parentWidget();inside_advanced=False
            while parent is not None:
                if parent in skipped:inside_advanced=True;break
                parent=parent.parentWidget()
            if not inside_advanced:
                text=widget.text()
                assert not any(word in text for word in forbidden),text
    for page in (control,maid,token):page.deleteLater()
    window.deleteLater();app.processEvents()
