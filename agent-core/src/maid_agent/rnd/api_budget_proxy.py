from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from maid_agent.config import ProviderProfile
from maid_agent.llm.openai_compatible import estimate_tokens, join_url
from maid_agent.memory.store import MemoryStore
from maid_agent.tokens.budget_guard import BudgetExceeded, BudgetGuard
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


PHASE_PURPOSES = {
    "RESEARCH": "rnd_research",
    "DESIGN": "rnd_design",
    "DEVELOPMENT": "rnd_development",
    "BUILD_FIX": "rnd_build",
    "FINALIZE": "rnd_finalize",
}


class _UsageCollector:
    def __init__(self, content_type: str) -> None:
        self.sse = "text/event-stream" in content_type.lower()
        self.pending = bytearray()
        self.body = bytearray()
        self.usage: dict[str, Any] | None = None
        self.response_bytes = 0

    def feed(self, chunk: bytes) -> None:
        self.response_bytes += len(chunk)
        if not self.sse:
            if len(self.body) < 16 * 1024 * 1024:
                self.body.extend(chunk[: 16 * 1024 * 1024 - len(self.body)])
            return
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            self._consume_sse_line(bytes(line).rstrip(b"\r"))

    def finish(self) -> None:
        if self.sse:
            if self.pending:
                self._consume_sse_line(bytes(self.pending).rstrip(b"\r"))
            return
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            self.usage = value["usage"]

    def _consume_sse_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            return
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            self.usage = value["usage"]


class _ProxyHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(self, address: tuple[str, int], proxy: RndApiBudgetProxy) -> None:
        self.proxy = proxy
        super().__init__(address, _ProxyHandler)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self.server.proxy._handle(self)  # type: ignore[attr-defined]


class RndApiBudgetProxy:
    """Loopback-only DSH API seam with the existing R&D BudgetGuard and TokenLedger."""

    def __init__(
        self,
        *,
        profile: ProviderProfile,
        api_key: str,
        ledger: TokenLedger,
        budget_guard: BudgetGuard,
        store: MemoryStore | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.profile = profile
        self._api_key = api_key
        self.ledger = ledger
        self.budget_guard = budget_guard
        self.store = store or ledger.store
        self.transport = transport
        self.cycle_id = ""
        self.phase = "DEVELOPMENT"
        self._token = ""
        self._server: _ProxyHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._request_lock = threading.Lock()

    def readiness(self) -> dict[str, Any]:
        return {
            "available": bool(self.profile and self._api_key),
            "host": "127.0.0.1",
            "running": self._server is not None,
            "cycle_id": self.cycle_id or None,
            "phase": self.phase,
            "model": self.profile.model,
            "ledger": "rnd",
        }

    async def start(self, *, cycle_id: str, phase: str) -> dict[str, str]:
        if self._server is not None:
            if self.cycle_id != cycle_id:
                raise RuntimeError("R&D API proxy already belongs to another cycle")
            self.set_phase(phase)
            return self.model_environment()
        if not self._api_key:
            raise RuntimeError("R&D API credential is unavailable")
        self.cycle_id = cycle_id
        self.set_phase(phase)
        self._token = secrets.token_urlsafe(32)
        self._server = _ProxyHttpServer(("127.0.0.1", 0), self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"rnd-api-proxy-{cycle_id}",
            daemon=True,
        )
        self._thread.start()
        return self.model_environment()

    def set_phase(self, phase: str) -> None:
        normalized = str(phase or "DEVELOPMENT").upper()
        self.phase = normalized if normalized in PHASE_PURPOSES else "DEVELOPMENT"

    def model_environment(self) -> dict[str, str]:
        if self._server is None or not self._token:
            raise RuntimeError("R&D API proxy is not running")
        base = f"http://127.0.0.1:{self._server.server_port}"
        return {
            "DEEPSEEK_BASE_URL": base,
            # Search is intentionally routed to the same budget seam. Unsupported
            # non-chat routes are rejected locally instead of bypassing the ledger.
            "DEEPSEEK_SEARCH_BASE_URL": base,
            "DEEPSEEK_API_KEY": self._token,
            "DEEPSEEK_MODEL": self.profile.model,
        }

    async def close(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self._token = ""
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            await asyncio.to_thread(server.server_close)
        if thread is not None:
            await asyncio.to_thread(thread.join, 5)
        self.cycle_id = ""

    def _handle(self, handler: _ProxyHandler) -> None:
        handler.close_connection = True
        if handler.client_address[0] not in {"127.0.0.1", "::1"}:
            self._send_json(handler, 403, "RND_PROXY_LOOPBACK_ONLY", "仅允许本机 DSH 使用研发代理")
            return
        authorization = handler.headers.get("authorization", "")
        expected = f"Bearer {self._token}"
        if not self._token or not hmac.compare_digest(authorization, expected):
            self._send_json(handler, 401, "RND_PROXY_UNAUTHORIZED", "研发代理令牌无效")
            return
        path = urlsplit(handler.path).path.rstrip("/")
        if path not in {"/chat/completions", "/v1/chat/completions"}:
            self._send_json(
                handler, 400, "RND_PROXY_ROUTE_BLOCKED",
                "该 DSH 辅助模型路由未接入研发 Token 账本，已阻止调用",
            )
            return
        try:
            length = int(handler.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 32 * 1024 * 1024:
            self._send_json(handler, 413, "RND_PROXY_REQUEST_SIZE", "研发模型请求大小无效")
            return
        try:
            payload = json.loads(handler.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(handler, 400, "RND_PROXY_INVALID_JSON", "研发模型请求不是有效 JSON")
            return
        if not isinstance(payload, dict):
            self._send_json(handler, 400, "RND_PROXY_INVALID_BODY", "研发模型请求必须是 JSON 对象")
            return
        with self._request_lock:
            self._proxy_chat(handler, payload)

    def _proxy_chat(self, handler: _ProxyHandler, payload: dict[str, Any]) -> None:
        request_id = "dsh-proxy-" + uuid4().hex
        started = time.monotonic()
        purpose = PHASE_PURPOSES[self.phase]
        prompt_estimate = estimate_tokens(
            list(payload.get("messages") or []), list(payload.get("tools") or []) or None,
        )
        desired = payload.get("max_tokens")
        if not isinstance(desired, int) or isinstance(desired, bool) or desired < 1:
            desired = self.budget_guard.rnd_settings.max_single_request
        try:
            _, completion_cap = self.budget_guard.limit_rnd_request(
                cycle_id=self.cycle_id,
                prompt_tokens=prompt_estimate,
                purpose=purpose,
                desired_completion_tokens=min(desired, self.budget_guard.rnd_settings.max_single_request),
            )
        except BudgetExceeded as exc:
            self._record_request(
                request_id=request_id, purpose=purpose, started=started, http_status=400,
                ok=False, prompt_tokens=prompt_estimate, completion_tokens=0,
                total_tokens=0, estimated=True, error_code=exc.code,
            )
            self._send_json(handler, 400, exc.code, str(exc))
            return

        upstream_payload = dict(payload)
        upstream_payload["model"] = self.profile.model
        upstream_payload["max_tokens"] = completion_cap
        if upstream_payload.get("stream") is True:
            stream_options = dict(upstream_payload.get("stream_options") or {})
            stream_options["include_usage"] = True
            upstream_payload["stream_options"] = stream_options
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            **self.profile.extra_headers,
        }
        url = join_url(self.profile.base_url, self.profile.chat_completions_path)
        sent_headers = False
        try:
            timeout = httpx.Timeout(self.profile.timeout_seconds)
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                with client.stream("POST", url, headers=headers, json=upstream_payload) as response:
                    if response.status_code >= 400:
                        body = response.read()
                        self._record_request(
                            request_id=request_id, purpose=purpose, started=started,
                            http_status=response.status_code, ok=False,
                            prompt_tokens=prompt_estimate, completion_tokens=0,
                            total_tokens=0, estimated=True,
                            error_code=f"HTTP_{response.status_code}",
                        )
                        handler.send_response(response.status_code)
                        handler.send_header("content-type", response.headers.get("content-type", "application/json"))
                        handler.send_header("content-length", str(len(body)))
                        handler.send_header("connection", "close")
                        handler.end_headers()
                        handler.wfile.write(body)
                        return

                    content_type = response.headers.get("content-type", "text/event-stream")
                    collector = _UsageCollector(content_type)
                    handler.send_response(response.status_code)
                    handler.send_header("content-type", content_type)
                    handler.send_header("cache-control", "no-cache")
                    handler.send_header("connection", "close")
                    handler.end_headers()
                    sent_headers = True
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        collector.feed(chunk)
                        handler.wfile.write(chunk)
                        handler.wfile.flush()
                    collector.finish()
                    usage = TokenUsage.from_provider(
                        collector.usage,
                        prompt_fallback=prompt_estimate,
                        completion_fallback=max(1, collector.response_bytes // 4),
                    )
                    self.ledger.record(
                        ledger="rnd", purpose=purpose, model=self.profile.model,
                        request_id=request_id, usage=usage, cycle_id=self.cycle_id,
                    )
                    self._record_request(
                        request_id=request_id, purpose=purpose, started=started,
                        http_status=response.status_code, ok=True,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.total_tokens, estimated=usage.estimated,
                        error_code="",
                    )
        except (httpx.HTTPError, OSError) as exc:
            self._record_request(
                request_id=request_id, purpose=purpose, started=started,
                http_status=None, ok=False, prompt_tokens=prompt_estimate,
                completion_tokens=0, total_tokens=0, estimated=True,
                error_code=exc.__class__.__name__,
            )
            if not sent_headers:
                self._send_json(handler, 502, "RND_PROXY_UPSTREAM_FAILED", "研发模型上游请求失败")

    def _record_request(
        self,
        *,
        request_id: str,
        purpose: str,
        started: float,
        http_status: int | None,
        ok: bool,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated: bool,
        error_code: str,
    ) -> None:
        self.store.record_llm_request(
            request_id=request_id, ledger="rnd", purpose=purpose,
            model=self.profile.model, http_status=http_status, ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total_tokens, estimated=estimated,
            error_code=error_code, cycle_id=self.cycle_id, game_day=None,
        )

    @staticmethod
    def _send_json(handler: _ProxyHandler, status: int, code: str, message: str) -> None:
        body = json.dumps(
            {"error": {"code": code, "type": "maidai_rnd_budget", "message": message}},
            ensure_ascii=False,
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("content-type", "application/json; charset=utf-8")
        handler.send_header("content-length", str(len(body)))
        handler.send_header("connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
