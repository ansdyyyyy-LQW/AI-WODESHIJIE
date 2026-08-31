from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from maid_agent.rnd.dsh_events import DshRunResult, parse_dsh_message


EventHandler = Callable[[dict[str, Any]], None]


class DeepSeekHarnessAdapter:
    """Thin Python controller for the official Node DSH profile and JSONL driver."""

    def __init__(
        self,
        *,
        node_executable: Path | str | None,
        launcher_path: Path | str,
        dsh_home: Path,
        log_root: Path,
        model_environment: Mapping[str, str] | None = None,
        event_handler: EventHandler | None = None,
        start_timeout: float = 900,
        phase_timeout: float = 1800,
    ) -> None:
        self.node_executable = str(node_executable or "")
        self.launcher_path = Path(launcher_path)
        self.dsh_home = Path(dsh_home)
        self.log_root = Path(log_root)
        self.model_environment = dict(model_environment or {})
        self.event_handler = event_handler
        self.start_timeout = start_timeout
        self.phase_timeout = phase_timeout
        self.process: asyncio.subprocess.Process | None = None
        self.workspace: Path | None = None
        self.session_id = ""
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_path: Path | None = None
        self._command_lock = asyncio.Lock()

    @classmethod
    def discover(
        cls,
        *,
        project_root: Path | None,
        dsh_home: Path,
        log_root: Path,
        model_environment: Mapping[str, str] | None = None,
        event_handler: EventHandler | None = None,
    ) -> DeepSeekHarnessAdapter:
        node = os.environ.get("MAIDAI_DSH_NODE", "")
        launcher = os.environ.get("MAIDAI_DSH_LAUNCHER", "")
        executable_root = Path(sys.executable).resolve().parent
        packaged_roots = [executable_root]
        if len(executable_root.parents) > 1:
            # The frozen agent lives under resources/MaidAgent while the shared
            # runtime belongs to the product root beside Maid AI Control.exe.
            packaged_roots.append(executable_root.parents[1])
        if not node:
            packaged_nodes = [root / "_internal" / "dsh" / "node" / "node.exe" for root in packaged_roots]
            packaged = next((path for path in packaged_nodes if path.is_file()), packaged_nodes[0])
            node = str(packaged if packaged.is_file() else (shutil.which("node") or ""))
        if not launcher:
            packaged_launchers = [
                root / "_internal" / "dsh" / "maidai-profile" / "lib" / "launcher.js"
                for root in packaged_roots
            ]
            packaged = next((path for path in packaged_launchers if path.is_file()), packaged_launchers[0])
            if packaged.is_file():
                launcher = str(packaged)
            elif project_root is not None:
                launcher = str(project_root / "dsh-integration" / "lib" / "launcher.js")
            else:
                launcher = str(packaged)
        return cls(
            node_executable=node,
            launcher_path=launcher,
            dsh_home=dsh_home,
            log_root=log_root,
            model_environment=model_environment,
            event_handler=event_handler,
        )

    def readiness(self) -> dict[str, Any]:
        missing: list[str] = []
        raw: dict[str, Any] = {}
        node = Path(self.node_executable) if self.node_executable else None
        if node is None or not node.is_file():
            resolved = shutil.which(self.node_executable) if self.node_executable else None
            if resolved:
                self.node_executable = resolved
            else:
                missing.append("node_runtime")
        if not self.launcher_path.is_file():
            missing.append("maidai_dsh_driver")
        lock = self.launcher_path.parents[1] / "references" / "DEEPSEEK_HARNESS_LOCK.json"
        if not lock.is_file():
            project_lock = self.launcher_path.parents[2] / "references" / "DEEPSEEK_HARNESS_LOCK.json" if len(self.launcher_path.parents) > 2 else lock
            lock = project_lock if project_lock.is_file() else lock
        version = ""
        try:
            raw = json.loads(lock.read_text(encoding="utf-8"))
            version = str(raw.get("version") or "")
        except (OSError, json.JSONDecodeError, AttributeError):
            missing.append("harness_version_lock")
        return {
            "available": not missing,
            "missing": missing,
            "node": self.node_executable or None,
            "launcher": str(self.launcher_path),
            "dsh_home": str(self.dsh_home),
            "version": version,
            "profile": "maidai",
            "profile_version": str(raw.get("profile_version") or ""),
            "driver_version": str(raw.get("driver_version") or ""),
            "cli_version": version,
            "sandbox": "workspace-write",
        }

    def set_model_environment(self, values: Mapping[str, str]) -> None:
        self.model_environment = dict(values)

    async def probe_startup(self, workspace: Path) -> dict[str, Any]:
        """Boot the pinned profile/driver in a disposable private workspace."""
        workspace = Path(workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        marker = workspace / "README.txt"
        if not marker.exists():
            marker.write_text("MaidAI DeepSeek Harness readiness workspace\n", encoding="utf-8")
        try:
            await self._launch(workspace, "maidai-dsh-readiness")
            return {**self.readiness(), "startup": True, "workspace": str(workspace)}
        finally:
            await self.terminate()

    def _environment(self) -> dict[str, str]:
        allowed = {
            "PATH", "USERPROFILE", "TEMP", "TMP", "SYSTEMROOT", "WINDIR",
            "COMSPEC", "PATHEXT", "JAVA_HOME", "GRADLE_USER_HOME", "LANG",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update({
            "DSH_HOME": str(self.dsh_home),
            "DSH_PERMISSION_MODE": "workspace-write",
            "DSH_TELEMETRY_DISABLED": "1",
        })
        # Formal operation supplies only the localhost proxy URL and its temporary token.
        for key in (
            "DEEPSEEK_BASE_URL", "DEEPSEEK_SEARCH_BASE_URL",
            "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
        ):
            value = self.model_environment.get(key)
            if value:
                env[key] = value
        return env

    async def _drain_stderr(self, stream: asyncio.StreamReader, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                handle.write(chunk)
                handle.flush()

    async def _launch(self, workspace: Path, session_id: str) -> None:
        if self.process is not None and self.process.returncode is None:
            if self.workspace != workspace or self.session_id != session_id:
                raise RuntimeError("one DSH driver process may serve only one cycle workspace")
            return
        ready = self.readiness()
        if not ready["available"]:
            raise RuntimeError("AI研发环境无法启动：" + ", ".join(ready["missing"]))
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"R&D workspace is missing: {workspace}")
        self.dsh_home.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.session_id = session_id
        self._stderr_path = self.log_root / f"{session_id}.stderr.log"
        self.process = await asyncio.create_subprocess_exec(
            self.node_executable, str(self.launcher_path),
            cwd=workspace, env=self._environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            limit=1024 * 1024,
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(self.process.stderr, self._stderr_path),
            name=f"dsh-stderr-{session_id}",
        )
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=self.start_timeout)
        except asyncio.TimeoutError as exc:
            await self._kill_process()
            raise RuntimeError("DSH driver startup timed out") from exc
        if not line:
            code = await self.process.wait()
            raise RuntimeError(f"DSH driver exited during startup with code {code}")
        message = parse_dsh_message(line)
        if message.type != "ready":
            raise RuntimeError(f"DSH driver did not send ready: {message.payload}")

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.returncode is not None or self.process.stdin is None:
            raise RuntimeError("DSH driver is not running")
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def _wait_result(self, request_id: str, *, phase: str) -> DshRunResult:
        if self.process is None or self.process.stdout is None or self.workspace is None:
            raise RuntimeError("DSH driver is not running")
        events: list[dict[str, Any]] = []
        while True:
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=self.phase_timeout)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(f"DSH phase timed out: {phase}") from exc
            if not line:
                code = await self.process.wait()
                return DshRunResult(
                    False, "DRIVER_EXITED", "error", f"DSH driver exited with code {code}",
                    self.session_id, str(self.workspace), phase, events=events,
                )
            message = parse_dsh_message(line)
            if message.request_id and message.request_id != request_id:
                continue
            if message.type in {"status", "event", "ready"}:
                events.append(message.payload)
                if self.event_handler is not None:
                    self.event_handler({"session_id": self.session_id, **message.payload})
                continue
            if message.type == "error":
                return DshRunResult(
                    False, str(message.payload.get("code") or "DSH_ERROR"), "error",
                    str(message.payload.get("message") or "DeepSeek Harness failed"),
                    self.session_id, str(self.workspace), phase, events=events, raw=message.payload,
                )
            if message.type == "result":
                reason = str(message.payload.get("finish_reason") or "unknown")
                usage_raw = message.payload.get("usage") or {}
                usage = {
                    str(key): int(value) for key, value in usage_raw.items()
                    if isinstance(value, int) and not isinstance(value, bool)
                } if isinstance(usage_raw, dict) else {}
                return DshRunResult(
                    reason == "completed", "SUCCESS" if reason == "completed" else reason.upper(),
                    reason, str(message.payload.get("summary") or ""), self.session_id,
                    str(self.workspace), phase, usage=usage, events=events, raw=message.payload,
                )

    async def _run(self, command: str, *, session_id: str, workspace: Path, task: str, phase: str) -> DshRunResult:
        async with self._command_lock:
            await self._launch(workspace, session_id)
            request_id = uuid4().hex
            await self._send({
                "type": command,
                "request_id": request_id,
                "session_id": session_id,
                "workspace": str(workspace.resolve()),
                "task": task,
                "phase": phase,
            })
            return await self._wait_result(request_id, phase=phase)

    async def start_cycle(self, *, session_id: str, workspace: Path, task: str, phase: str) -> DshRunResult:
        return await self._run("start", session_id=session_id, workspace=workspace, task=task, phase=phase)

    async def run_phase(self, *, session_id: str, workspace: Path, task: str, phase: str) -> DshRunResult:
        return await self._run("run_phase", session_id=session_id, workspace=workspace, task=task, phase=phase)

    async def resume(self, *, session_id: str, workspace: Path, task: str, phase: str) -> DshRunResult:
        return await self._run("resume", session_id=session_id, workspace=workspace, task=task, phase=phase)

    async def suspend(self) -> DshRunResult | None:
        if self.process is None or self.process.returncode is not None or self.workspace is None:
            return None
        async with self._command_lock:
            request_id = uuid4().hex
            await self._send({"type": "suspend", "request_id": request_id})
            result = await self._wait_result(request_id, phase="SUSPENDED")
            await self._close_streams()
            return result

    async def terminate(self) -> None:
        if self.process is None or self.process.returncode is not None:
            await self._close_streams()
            return
        async with self._command_lock:
            request_id = uuid4().hex
            with suppress(Exception):
                await self._send({"type": "terminate", "request_id": request_id})
                await self._wait_result(request_id, phase="TERMINATE")
            await self._close_streams()

    async def _close_streams(self) -> None:
        process = self.process
        if process is not None and process.stdin is not None:
            with suppress(BrokenPipeError, ConnectionResetError):
                process.stdin.close()
                await process.stdin.wait_closed()
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                if os.name == "nt" and process.pid:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(killer.wait(), timeout=5)
                else:
                    process.kill()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
        if self._stderr_task is not None:
            if not self._stderr_task.done():
                self._stderr_task.cancel()
            with suppress(asyncio.CancelledError, OSError):
                await self._stderr_task
        self.process = None
        self._stderr_task = None

    async def _kill_process(self) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        if os.name == "nt" and process.pid:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), timeout=5)
        else:
            process.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=5)
        await self._close_streams()
