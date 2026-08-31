from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from maid_agent.config import ProviderProfile, RndBudgetSettings, RuntimeBudgetSettings
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.api_budget_proxy import RndApiBudgetProxy
from maid_agent.rnd.dsh_adapter import DeepSeekHarnessAdapter
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.tokens.budget_guard import BudgetGuard
from maid_agent.tokens.ledger import TokenLedger


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(
            str(item.get("text") or "") for item in value if isinstance(item, dict)
        )
    return ""


@pytest.mark.asyncio
async def test_real_driver_suspends_and_resumes_same_session(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            requests.append(body)
            messages = body.get("messages") or []
            users = [item for item in messages if item.get("role") == "user"]
            latest_user = _text(users[-1].get("content")) if users else ""
            after_latest_user: list[dict[str, Any]] = []
            for item in reversed(messages):
                if item.get("role") == "user":
                    break
                after_latest_user.append(item)
            after_latest_user.reverse()
            tool_messages = [item for item in after_latest_user if item.get("role") == "tool"]

            if "RESUME_STEP" in latest_user:
                if len(tool_messages) == 0:
                    payload = self._tool("resume-read", "read", {"file_path": "resume-marker.txt"})
                elif len(tool_messages) == 1:
                    payload = self._tool(
                        "resume-edit", "edit",
                        {
                            "file_path": "resume-marker.txt",
                            "old_string": "first phase\n",
                            "new_string": "first phase\nresumed phase\n",
                        },
                    )
                else:
                    payload = self._text_response("same session resume complete")
            elif len(tool_messages) == 0:
                payload = self._tool(
                    "initial-write", "write",
                    {"file_path": "resume-marker.txt", "content": "first phase\n"},
                )
            else:
                payload = self._text_response("initial phase complete")

            encoded = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(encoded)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        @staticmethod
        def _tool(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": "mock-tool", "object": "chat.completion.chunk", "model": "deepseek-v4-flash",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0, "id": call_id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            }

        @staticmethod
        def _text_response(content: str) -> dict[str, Any]:
            return {
                "id": "mock-text", "object": "chat.completion.chunk", "model": "deepseek-v4-flash",
                "choices": [{
                    "index": 0, "delta": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 24, "completion_tokens": 6, "total_tokens": 30},
            }

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="mock-deepseek", daemon=True)
    thread.start()
    first: DeepSeekHarnessAdapter | None = None
    second: DeepSeekHarnessAdapter | None = None
    proxy: RndApiBudgetProxy | None = None
    try:
        root = Path(__file__).resolve().parents[2]
        launcher = root / "dsh-integration" / "lib" / "launcher.js"
        if not launcher.is_file():
            pytest.skip("build dsh-integration before running the integration test")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = MemoryStore(tmp_path / "state.sqlite3")
        cycle = RndTrigger(
            store, tmp_path / "handoff", cycle_days=5, token_budget=100_000,
        ).create(5)
        ledger = TokenLedger(store)
        guard = BudgetGuard(
            ledger, RuntimeBudgetSettings(enabled=False),
            RndBudgetSettings(budget_per_cycle=100_000, max_single_request=100_000),
        )
        proxy = RndApiBudgetProxy(
            profile=ProviderProfile(
                profile_id="dsh-integration", display_name="DSH integration",
                base_url=f"http://127.0.0.1:{server.server_port}",
                model="deepseek-v4-flash", max_retries=0,
            ),
            api_key="upstream-test-key", ledger=ledger,
            budget_guard=guard, store=store,
        )
        model_environment = await proxy.start(cycle_id=cycle.cycle_id, phase="DEVELOPMENT")
        session_id = f"maidai-rnd-{cycle.cycle_id}"
        common = {
            "node_executable": "C:/Program Files/nodejs/node.exe",
            "launcher_path": launcher,
            "dsh_home": tmp_path / "dsh-home",
            "log_root": tmp_path / "logs",
            "model_environment": model_environment,
            "start_timeout": 900,
            "phase_timeout": 120,
        }
        first = DeepSeekHarnessAdapter(**common)
        initial = await first.start_cycle(
            session_id=session_id, workspace=workspace,
            task="INITIAL_STEP create the requested marker", phase="DEVELOPMENT",
        )
        assert initial.ok and initial.session_id == session_id
        assert (workspace / "resume-marker.txt").read_text(encoding="utf-8") == "first phase\n"
        suspended = await first.suspend()
        assert suspended is not None and suspended.finish_reason == "suspended"

        second = DeepSeekHarnessAdapter(**common)
        resumed = await second.resume(
            session_id=session_id, workspace=workspace,
            task="RESUME_STEP continue the saved DEVELOPMENT phase", phase="DEVELOPMENT",
        )
        assert resumed.ok and resumed.session_id == session_id
        assert (workspace / "resume-marker.txt").read_text(encoding="utf-8") == (
            "first phase\nresumed phase\n"
        )
        assert any(
            "INITIAL_STEP" in json.dumps(request.get("messages") or [], ensure_ascii=False)
            and "RESUME_STEP" in json.dumps(request.get("messages") or [], ensure_ascii=False)
            for request in requests
        )
        assert ledger.total("rnd", cycle_id=cycle.cycle_id) > 0
        assert ledger.total("runtime") == 0
    finally:
        if second is not None:
            await second.terminate()
        if first is not None:
            await first.terminate()
        if proxy is not None:
            await proxy.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
