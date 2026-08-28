from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

from maid_ai_control.dpapi import DpapiSecretStore


def app_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "MaidAI"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "MaidAI"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


class ConfigManager:
    def __init__(self, path: Path | None = None, *, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else app_data_dir()
        self.path = Path(path) if path else self.data_dir / "config" / "agent.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._dpapi = DpapiSecretStore(self.data_dir / "secrets" / "dpapi.json")
        self.data = self.load()
        self.save()

    def defaults(self) -> dict[str, Any]:
        runtime = _runtime_root()
        packaged_source = runtime / "resources" / "rnd-source"
        development_source = runtime if (runtime / "agent-core").exists() else None
        packaged_runner = runtime / "resources" / "rnd-harness" / "maid-rnd.exe"
        development_runner = runtime / "rnd-runner" / "src" / "maid_rnd_runner" / "main.py"
        return {
            "host": "127.0.0.1",
            "bridge_port": 8765,
            "control_port": 8766,
            "control_token": "",
            "data_dir": str(self.data_dir),
            "autonomous_review_seconds": 90,
            "strict_survival": True,
            "auto_start": False,
            "setup_complete": False,
            "api_probes": {"runtime": {}, "rnd": {}},
            "minecraft_restart_required": False,
            "selected_owner_name": "",
            "runtime_profile": None,
            "rnd_profile": None,
            "runtime_budget": {
                "enabled": False,
                "max_per_game_day": None,
                "max_per_real_hour": None,
                "reserve_tokens": 4096,
            },
            "rnd_budget": {
                "budget_per_cycle": 100_000_000,
                "cycle_game_days": 5,
                "max_single_request": 2_000_000,
            },
            "minecraft_dir": "",
            "owner_uuid": "",
            "log_level": "INFO",
            "source_workspace": str(packaged_source if packaged_source.exists() else development_source or ""),
            "full_harness_runner_path": str(packaged_runner if packaged_runner.exists() else development_runner if development_runner.exists() else ""),
            "rnd_work_root": str(self.data_dir / "rnd-worktrees"),
        }

    def load(self) -> dict[str, Any]:
        data = self.defaults()
        if self.path.exists():
            try:
                incoming = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(incoming, dict):
                    self._deep_update(data, incoming)
            except Exception:
                # Keep the last known valid defaults and let the GUI save a repaired file.
                pass
        data["data_dir"] = str(self.data_dir)
        if not data.get("control_token"):
            data["control_token"] = secrets.token_urlsafe(32)
        return data

    @staticmethod
    def _deep_update(base: dict[str, Any], update: dict[str, Any]) -> None:
        for key, value in update.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                ConfigManager._deep_update(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        # Refresh product paths when a source checkout is moved or a packaged
        # directory is copied to another location.
        defaults = self.defaults()
        for key in ("source_workspace", "full_harness_runner_path", "rnd_work_root"):
            current = str(self.data.get(key) or "")
            if not current or (key != "rnd_work_root" and not Path(current).exists()):
                self.data[key] = defaults[key]
        self.data["data_dir"] = str(self.data_dir)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    def secret_get(self, secret_id: str) -> str:
        if not secret_id:
            return ""
        try:
            import keyring
            value = keyring.get_password("MaidAI", secret_id) or ""
            if value:
                return value
        except Exception:
            pass
        try:
            value = self._dpapi.get(secret_id) or ""
            if value:
                return value
        except Exception:
            pass
        return os.environ.get(
            "MAIDAI_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", secret_id).upper(), ""
        )

    def secret_set(self, secret_id: str, value: str) -> None:
        if not secret_id or not value:
            return
        try:
            import keyring
            keyring.set_password("MaidAI", secret_id, value)
            return
        except Exception:
            pass
        try:
            self._dpapi.set(secret_id, value)
        except Exception as exc:
            raise RuntimeError("系统凭据存储和 Windows 加密存储均不可用，API Key 未保存。") from exc

    @property
    def control_url(self) -> str:
        return f"ws://{self.data.get('host', '127.0.0.1')}:{int(self.data.get('control_port', 8766))}"

    @property
    def setup_complete(self) -> bool:
        return bool(self.data.get("setup_complete", False))
