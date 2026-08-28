from __future__ import annotations

import json
import tempfile
from pathlib import Path

from maid_ai_control.config import ConfigManager


def run_self_test() -> dict:
    checks: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="maidai-control-test-") as raw:
        root = Path(raw)
        config = ConfigManager(data_dir=root)
        config.data["minecraft_dir"] = str(root / "minecraft")
        config.save()
        loaded = ConfigManager(data_dir=root)
        checks["config_roundtrip"] = loaded.data["minecraft_dir"] == str(root / "minecraft")
        raw_json = json.dumps(loaded.data).lower()
        checks["no_plaintext_api_key"] = "sk-" not in raw_json and "api_key\"" not in raw_json
        checks["stable_ports"] = loaded.data["bridge_port"] == 8765 and loaded.data["control_port"] == 8766
        checks["control_token"] = bool(loaded.data.get("control_token"))
        checks["source_workspace_key"] = "source_workspace" in loaded.data
    return {"ok": all(bool(v) for v in checks.values()), "checks": checks}
