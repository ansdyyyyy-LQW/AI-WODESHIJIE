from __future__ import annotations

import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtWidgets import QApplication

from maid_ai_control.api_probe import probe_is_recent, run_probe
from maid_ai_control.config import ConfigManager
from maid_ai_control.minecraft_locator import inspect_minecraft_dir
from maid_ai_control.wizard import SetupWizard


def test_probe_sends_exact_model_and_persists_no_key(tmp_path: Path, monkeypatch) -> None:
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.update(json.loads(body))
            response = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = ConfigManager(tmp_path / "agent.json", data_dir=tmp_path / "data")
        monkeypatch.setattr(config, "secret_get", lambda _secret_id: "top-secret")
        profile = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "Model Name Must Stay Exact", "api_key_secret_id": "runtime-key", "chat_completions_path": "/chat/completions"}
        result = run_probe(config, "runtime", profile)
        assert result.last_probe_ok and received["model"] == "Model Name Must Stay Exact"
        assert probe_is_recent(config, "runtime", profile)
        assert "top-secret" not in config.path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_minecraft_detection_reads_mod_ids_inside_jars(tmp_path: Path) -> None:
    mods = tmp_path / "mods"
    mods.mkdir()
    for filename, mod_id, version in (("renamed-a.jar", "touhou_little_maid", "1.5.3"), ("renamed-b.jar", "maid_ai_bridge", "0.2.0")):
        with zipfile.ZipFile(mods / filename, "w") as archive:
            archive.writestr("META-INF/mods.toml", f'[[mods]]\nmodId="{mod_id}"\nversion="{version}"\ndisplayName="test"\n')
    result = inspect_minecraft_dir(tmp_path)
    assert result["ready"] is True
    assert result["tlm"]["jar"].endswith("renamed-a.jar")


def test_first_run_wizard_has_four_production_pages(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "agent.json", data_dir=tmp_path / "data")
    wizard = SetupWizard(config)
    assert len(wizard.pageIds()) == 4
    assert config.setup_complete is False
    wizard.deleteLater()
    app.processEvents()
