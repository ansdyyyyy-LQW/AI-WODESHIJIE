from __future__ import annotations
import json,os,sys,types
from pathlib import Path
import pytest
from maid_ai_control.config import ConfigManager


def test_config_round_trip_and_no_secret_value(tmp_path:Path,monkeypatch):
    secrets={}
    fake=types.SimpleNamespace(
        set_password=lambda service,user,value:secrets.__setitem__((service,user),value),
        get_password=lambda service,user:secrets.get((service,user)),
    )
    monkeypatch.setitem(sys.modules,"keyring",fake)
    cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data")
    cfg.data["runtime_profile"]={"profile_id":"runtime","display_name":"Runtime","base_url":"https://example.invalid/v1","model":"model-original","api_key_secret_id":"runtime-key"}
    cfg.secret_set("runtime-key","secret-value")
    cfg.save()
    assert cfg.secret_get("runtime-key")=="secret-value"
    assert secrets[("MaidAI","runtime-key")]=="secret-value"
    raw=(tmp_path/"agent.json").read_text(encoding="utf-8")
    assert "secret-value" not in raw
    assert json.loads(raw)["runtime_profile"]["model"]=="model-original"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI fallback")
def test_keyring_failure_uses_dpapi_without_plaintext(tmp_path:Path,monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("keyring unavailable")
    fake=types.SimpleNamespace(set_password=unavailable,get_password=unavailable)
    monkeypatch.setitem(sys.modules,"keyring",fake)
    cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data")
    cfg.secret_set("rnd-key","dpapi-secret-value")
    assert cfg.secret_get("rnd-key")=="dpapi-secret-value"
    encrypted=(tmp_path/"data"/"secrets"/"dpapi.json").read_text(encoding="utf-8")
    assert "dpapi-secret-value" not in encrypted
