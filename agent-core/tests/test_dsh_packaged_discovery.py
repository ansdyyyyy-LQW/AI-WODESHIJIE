from __future__ import annotations

import json
from pathlib import Path

from maid_agent.rnd.dsh_adapter import DeepSeekHarnessAdapter


def test_frozen_agent_finds_dsh_at_shared_product_root(tmp_path: Path, monkeypatch) -> None:
    product = tmp_path / "MaidAI"
    agent = product / "resources" / "MaidAgent" / "maid-agent.exe"
    node = product / "_internal" / "dsh" / "node" / "node.exe"
    profile = product / "_internal" / "dsh" / "maidai-profile"
    launcher = profile / "lib" / "launcher.js"
    lock = profile / "references" / "DEEPSEEK_HARNESS_LOCK.json"
    for path in (agent, node, launcher):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"version": "0.1.1-rc.2"}), encoding="utf-8")
    monkeypatch.setattr("maid_agent.rnd.dsh_adapter.sys.executable", str(agent))

    adapter = DeepSeekHarnessAdapter.discover(
        project_root=None, dsh_home=tmp_path / "home", log_root=tmp_path / "logs",
    )

    assert Path(adapter.node_executable) == node
    assert adapter.launcher_path == launcher
    assert adapter.readiness()["available"] is True
