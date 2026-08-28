from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from websockets.sync.client import connect as websocket_connect

from maid_ai_control.config import ConfigManager


class ProcessSupervisor(QObject):
    stateChanged = Signal(str)
    output = Signal(str)

    def __init__(self, config: ConfigManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.stateChanged.connect(lambda _: self.stateChanged.emit(self.status()))
        self.bridge_port_changed = False

    @property
    def host(self) -> str:
        return str(self.config.data.get("host") or "127.0.0.1")

    def status(self) -> str:
        if self.process.state() == QProcess.Running:
            return "RUNNING"
        return "EXTERNAL" if self.control_identity() else "STOPPED"

    @staticmethod
    def _port_in_use(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    def control_identity(self, port: int | None = None) -> dict | None:
        """Require an authenticated MaidAI GET_STATUS, not merely an open port."""
        port = int(port or self.config.data.get("control_port", 8766))
        request_id = str(uuid4())
        try:
            with websocket_connect(
                f"ws://{self.host}:{port}", open_timeout=0.7, close_timeout=0.2
            ) as ws:
                ws.send(
                    json.dumps(
                        {
                            "request_id": request_id,
                            "command": "GET_STATUS",
                            "args": {},
                            "token": str(self.config.data.get("control_token") or ""),
                        }
                    )
                )
                deadline=time.monotonic()+1.5
                response={}
                while time.monotonic()<deadline:
                    candidate=json.loads(ws.recv(timeout=max(.05,deadline-time.monotonic())))
                    if candidate.get("request_id")==request_id:
                        response=candidate
                        break
        except Exception:
            return None
        identity = dict((response.get("data") or {}).get("control_identity") or {})
        if (
            response.get("type") == "CONTROL_RESULT"
            and response.get("request_id") == request_id
            and response.get("ok") is True
            and identity.get("product") == "MaidAI-Agent"
            and int(identity.get("protocol_version") or 0) == 1
            and str(identity.get("instance_id") or "")
        ):
            return identity
        return None

    def port_open(self) -> bool:
        """Compatibility name: true only for an authenticated MaidAI Agent."""
        return self.control_identity() is not None

    def find_free_port(self, preferred: int, *, exclude: set[int] | None = None) -> int:
        exclude = exclude or set()
        for candidate in range(max(1024, preferred), min(65535, preferred + 256) + 1):
            if candidate in exclude:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                    probe.bind((self.host, candidate))
                return candidate
            except OSError:
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((self.host, 0))
            candidate = int(probe.getsockname()[1])
        return candidate if candidate not in exclude else self.find_free_port(candidate + 1, exclude=exclude)

    @staticmethod
    def minecraft_running() -> bool:
        if os.name != "nt":
            return False
        command = (
            "$rows=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Where-Object {$_.Name -in @('java.exe','javaw.exe')} | "
            "Select-Object -ExpandProperty CommandLine; $rows -join \"`n\""
        )
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        text = result.stdout.lower()
        return any(token in text for token in ("net.minecraft", "cpw.mods", "forge_client", "minecraftlauncher"))

    def _agent_command(self) -> tuple[str, list[str]]:
        root = Path(sys.executable).resolve().parent
        candidates = [
            root / "resources" / "MaidAgent" / "maid-agent.exe",
            Path(sys.argv[0]).resolve().parent / "resources" / "MaidAgent" / "maid-agent.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate), ["--config", str(self.config.path)]
        project = Path(__file__).resolve()
        for parent in project.parents:
            if (parent / "agent-core" / "src" / "maid_agent").exists():
                env = os.environ.copy()
                env["PYTHONPATH"] = str(parent / "agent-core" / "src")
                self.process.setProcessEnvironment(self._qt_env(env))
                return sys.executable, ["-m", "maid_agent.main", "--config", str(self.config.path)]
        return sys.executable, ["-m", "maid_agent.main", "--config", str(self.config.path)]

    @staticmethod
    def _qt_env(env: dict[str, str]) -> QProcessEnvironment:
        result = QProcessEnvironment()
        for key, value in env.items():
            result.insert(key, value)
        return result

    def _resolve_ports(self) -> None:
        control = int(self.config.data.get("control_port", 8766))
        bridge = int(self.config.data.get("bridge_port", 8765))
        if not self.control_identity(control) and self._port_in_use(self.host, control):
            control = self.find_free_port(control + 1, exclude={bridge})
            self.config.data["control_port"] = control
        if self._port_in_use(self.host, bridge):
            replacement = self.find_free_port(bridge + 1, exclude={control})
            self.bridge_port_changed = replacement != bridge
            self.config.data["bridge_port"] = replacement
            if self.bridge_port_changed and self.minecraft_running():
                self.config.data["minecraft_restart_required"] = True
        self.config.save()

    def start_agent(self) -> None:
        self.config.save()
        if self.control_identity():
            self.stateChanged.emit("EXTERNAL")
            return
        self._resolve_ports()
        self.sync_bridge_config()
        program, args = self._agent_command()
        self.process.setProgram(program)
        self.process.setArguments(args)
        self.process.setWorkingDirectory(str(self.config.data_dir))
        self.process.start()
        self.stateChanged.emit("STARTING")

    def restart_agent(self) -> None:
        self.stop_agent()
        self.process.waitForFinished(5000)
        self.start_agent()

    def stop_agent(self) -> None:
        if self.process.state() != QProcess.Running:
            return
        request_id = str(uuid4())
        acknowledged = False
        try:
            with websocket_connect(
                f"ws://{self.host}:{int(self.config.data.get('control_port',8766))}",
                open_timeout=.7,
                close_timeout=.2,
            ) as ws:
                ws.send(json.dumps({
                    "request_id":request_id,
                    "command":"SHUTDOWN",
                    "args":{},
                    "token":str(self.config.data.get("control_token") or ""),
                }))
                # SHUTDOWN responds only after Runtime and an owned R&D task have
                # persisted their terminal state; do not force-kill that cleanup.
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    response=json.loads(ws.recv(timeout=max(.05,deadline-time.monotonic())))
                    if response.get("request_id")==request_id:
                        acknowledged=response.get("ok") is True and bool((response.get("data") or {}).get("shutting_down"))
                        break
        except Exception:
            acknowledged=False
        if acknowledged and self.process.waitForFinished(8000):
            return
        pid=int(self.process.processId())
        if os.name=="nt" and pid>0:
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
            try:
                subprocess.run(
                    ["taskkill","/PID",str(pid),"/T","/F"],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,
                    startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW,check=False,
                )
            except (OSError,subprocess.SubprocessError):
                pass
            self.process.waitForFinished(3000)
        else:
            self.process.terminate();self.process.waitForFinished(3000)
        if self.process.state()==QProcess.Running:
            self.process.kill();self.process.waitForFinished(3000)

    def _read(self) -> None:
        self.output.emit(bytes(self.process.readAllStandardOutput()).decode(errors="replace"))

    def sync_bridge_config(self) -> Path | None:
        raw = str(self.config.data.get("minecraft_dir") or "").strip()
        if not raw:
            return None
        cfg = Path(raw) / "config" / "maid_ai_bridge-common.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        port = int(self.config.data.get("bridge_port", 8765))
        text = f'''# Managed by Maid AI Control. Restart Minecraft after changing this file.
[connection]
websocket_url = "ws://{self.host}:{port}"
snapshot_hz = 2

[observation]
entity_observe_radius = 32
visible_block_radius = 12
allow_hidden_block_scan = false

[capability_policy]
strict_survival = true
allow_remote_world_edit = false
action_timeout_ticks = 1200
'''
        temp = cfg.with_suffix(".tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, cfg)
        return cfg
