from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.dsh_adapter import DeepSeekHarnessAdapter
from maid_agent.rnd.harness import RndHarness
from maid_agent.rnd.trigger import RndTrigger


ROOT = Path(__file__).resolve().parents[2]


def _platform_calc_check(calc_file: Path) -> str:
    if os.name == "nt":
        quoted = str(calc_file).replace("'", "''")
        return (
            "$ErrorActionPreference = 'Stop'; "
            f"$source = Get-Content -Raw -LiteralPath '{quoted}'; "
            "if (-not $source.Contains('return a + b;')) { "
            "throw 'calc acceptance failed' }; "
            "Write-Output 'calc acceptance passed'"
        )
    return f"grep -Fq 'return a + b;' {shlex.quote(str(calc_file))}"


def _platform_git_diff(workspace: Path) -> str:
    marker = "docs/dsh_workspace_isolation_acceptance.md"
    if os.name == "nt":
        quoted = str(workspace).replace("'", "''")
        return f"git -C '{quoted}' add -N -- {marker}; git -C '{quoted}' diff -- {marker}"
    quoted = shlex.quote(str(workspace))
    return f"git -C {quoted} add -N -- {marker} && git -C {quoted} diff -- {marker}"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(row.get("text") or "") for row in value if isinstance(row, dict))
    return ""


def _tool(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "object": "chat.completion.chunk",
        "model": "deepseek-v4-flash",
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
        "usage": {"prompt_tokens": 25, "completion_tokens": 10, "total_tokens": 35},
    }


def _done(content: str) -> dict[str, Any]:
    return {
        "id": "acceptance-done",
        "object": "chat.completion.chunk",
        "model": "deepseek-v4-flash",
        "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 25, "completion_tokens": 8, "total_tokens": 33},
    }


@pytest.mark.asyncio
async def test_official_dsh_edits_runs_failed_test_then_fixes_and_keeps_production_isolated(
    tmp_path: Path,
) -> None:
    launcher = ROOT / "dsh-integration" / "lib" / "launcher.js"
    if not launcher.is_file():
        pytest.skip("build dsh-integration before running the integration acceptance")
    node = shutil.which("node")
    assert node is not None, "Node runtime is required for the real DSH integration acceptance"
    shell_tool = "pwsh" if os.name == "nt" else "bash"
    requests: list[dict[str, Any]] = []
    emitted_tools: list[str] = []
    active = {"mode": "calc_fail"}
    call_counts = {"calc_fail": 0, "calc_fix": 0, "maidai": 0}
    outside_target = ROOT / "docs" / "dsh_outside_workspace_write_must_fail.txt"
    assert not outside_target.exists()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            requests.append(body)
            if not body.get("tools"):
                # DSH creates a title with a separate, tool-free model request.
                # It must not consume one of the scripted production tool steps.
                payload = _done("MaidAI DSH acceptance")
            else:
                mode = active["mode"]
                index = call_counts[mode]
                call_counts[mode] += 1
                if mode == "calc_fail":
                    steps = [
                        _tool("calc-read", "read", {"file_path": "calc.js"}),
                        _tool("calc-outside", "write", {
                            "file_path": str(outside_target),
                            "content": "this write must be rejected\n",
                        }),
                        _tool("calc-fail", shell_tool, {
                            "command": _platform_calc_check(calc / "calc.js"),
                            "description": "Run failing calculation test",
                            "workdir": ".",
                        }),
                    ]
                    payload = steps[index] if index < len(steps) else _done("failing test captured")
                elif mode == "calc_fix":
                    steps = [
                        _tool("calc-reread", "read", {"file_path": "calc.js"}),
                        _tool("calc-edit", "edit", {
                            "file_path": "calc.js",
                            "old_string": "  return a - b;\n",
                            "new_string": "  return a + b;\n",
                        }),
                        _tool("calc-pass", shell_tool, {
                            "command": _platform_calc_check(calc / "calc.js"),
                            "description": "Run repaired calculation test",
                            "workdir": ".",
                        }),
                    ]
                    payload = steps[index] if index < len(steps) else _done("calc fixed and tested")
                else:
                    steps = [
                        _tool("maidai-write", "write", {
                            "file_path": "docs/dsh_workspace_isolation_acceptance.md",
                            "content": "DSH isolated workspace acceptance\n",
                        }),
                        _tool("maidai-diff", shell_tool, {
                            "command": _platform_git_diff(workspace),
                            "description": "Show isolated workspace source diff",
                            "workdir": ".",
                        }),
                    ]
                    payload = steps[index] if index < len(steps) else _done("isolated workspace changed")

            tool_calls = payload.get("choices", [{}])[0].get("delta", {}).get("tool_calls", [])
            if tool_calls:
                emitted_tools.append(str(tool_calls[0].get("function", {}).get("name") or ""))

            encoded = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(encoded)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="dsh-coding-acceptance", daemon=True)
    thread.start()
    adapters: list[DeepSeekHarnessAdapter] = []
    workspace_parent = ROOT / ".test-tmp"
    workspace_parent.mkdir(exist_ok=True)
    workspace_temp = TemporaryDirectory(prefix="dsh-coding-", dir=workspace_parent)
    isolated_root = Path(workspace_temp.name)
    environment = {
        "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "DEEPSEEK_SEARCH_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "DEEPSEEK_API_KEY": "local-acceptance-token",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
    }

    def adapter(name: str) -> DeepSeekHarnessAdapter:
        value = DeepSeekHarnessAdapter(
            node_executable=node,
            launcher_path=launcher,
            dsh_home=tmp_path / "dsh-home" / name,
            log_root=tmp_path / "logs",
            model_environment=environment,
            start_timeout=900,
            phase_timeout=180,
        )
        adapters.append(value)
        return value

    try:
        calc = isolated_root / "calc-project"
        calc.mkdir()
        (calc / "calc.js").write_text(
            "exports.add = function add(a, b) {\n  return a - b;\n};\n",
            encoding="utf-8",
        )
        (calc / "test_calc.mjs").write_text(
            'import test from "node:test";\n'
            'import assert from "node:assert/strict";\n'
            'import calc from "./calc.js";\n\n'
            'test("add", () => assert.equal(calc.add(2, 3), 5));\n',
            encoding="utf-8",
        )
        initial = subprocess.run(
            [node, "--test", "test_calc.mjs"], cwd=calc,
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        assert initial.returncode != 0, initial.stdout + initial.stderr
        calc_adapter = adapter("calc")
        calc_result = await calc_adapter.start_cycle(
            session_id="acceptance-calc",
            workspace=calc,
            task="CALC_ACCEPTANCE open the code and run the currently failing test",
            phase="DEVELOPMENT",
        )
        assert not calc_result.ok
        assert calc_result.finish_reason == "tool_failed"
        assert calc_result.raw["tool_state"]["ok"] is False
        assert {row["tool"] for row in calc_result.raw["tool_state"]["unresolved_failures"]} >= {
            "write", shell_tool,
        }
        assert not outside_target.exists()
        active["mode"] = "calc_fix"
        fix_result = await calc_adapter.run_phase(
            session_id="acceptance-calc",
            workspace=calc,
            task="CALC_ACCEPTANCE fix the failed calculation and rerun the test",
            phase="BUILD_FIX",
        )
        assert fix_result.ok
        assert fix_result.raw["tool_state"]["ok"] is True
        assert fix_result.raw["tool_state"]["workspace_changed"] is True
        assert "return a + b" in (calc / "calc.js").read_text(encoding="utf-8"), (
            f"calls={call_counts!r} tools={emitted_tools!r} raw={fix_result.raw!r}"
        )
        completed = subprocess.run(
            [node, "--test", "test_calc.mjs"], cwd=calc,
            text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

        production_marker = ROOT / "docs" / "dsh_workspace_isolation_acceptance.md"
        assert not production_marker.exists()
        readme = ROOT / "README_CN.txt"
        production_hash = hashlib.sha256(readme.read_bytes()).hexdigest()
        store = MemoryStore(tmp_path / "state.sqlite3")
        cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000).create(5)
        harness = RndHarness(
            runner_path=str(ROOT / "rnd-runner" / "src" / "maid_rnd_runner" / "main.py"),
            source_workspace=ROOT,
            work_root=isolated_root / "workspaces",
        )
        state = harness.prepare_workspace(cycle)
        workspace = Path(state["workspace"])
        active["mode"] = "maidai"
        maidai_result = await adapter("maidai").start_cycle(
            session_id="acceptance-maidai-isolation",
            workspace=workspace,
            task="MAIDAI_ISOLATION add the requested harmless documentation marker and show its git diff",
            phase="DEVELOPMENT",
        )
        assert maidai_result.ok
        assert maidai_result.raw["tool_state"]["ok"] is True
        assert "docs/dsh_workspace_isolation_acceptance.md" in maidai_result.raw["tool_state"]["changed_paths"]
        marker = workspace / "docs" / "dsh_workspace_isolation_acceptance.md"
        assert marker.read_text(encoding="utf-8") == "DSH isolated workspace acceptance\n"
        subprocess.run(
            ["git", "add", "-N", "--", "docs/dsh_workspace_isolation_acceptance.md"],
            cwd=workspace, check=True,
        )
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", str(state["baseline_commit"])],
            cwd=workspace, text=True, encoding="utf-8",
        )
        assert "docs/dsh_workspace_isolation_acceptance.md" in diff.replace("\\", "/")
        assert not production_marker.exists()
        assert hashlib.sha256(readme.read_bytes()).hexdigest() == production_hash

        assert call_counts["calc_fail"] >= 3
        assert call_counts["calc_fix"] >= 4
        assert call_counts["maidai"] >= 3
        assert emitted_tools == [
            "read", "write", shell_tool, "read", "edit", shell_tool, "write", shell_tool,
        ]
    finally:
        for value in reversed(adapters):
            await value.terminate()
        server.shutdown()
        server.server_close()
        await asyncio.to_thread(thread.join, 5)
        workspace_temp.cleanup()
