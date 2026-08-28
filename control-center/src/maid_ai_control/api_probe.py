from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from maid_ai_control.config import ConfigManager


@dataclass(frozen=True)
class ApiProbeResult:
    last_probe_ok: bool
    last_probe_at: int
    latency_ms: int
    http_status: int | None
    error_summary: str
    profile_signature: str
    model: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def profile_signature(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    raw = "|".join(
        (
            str(profile.get("base_url") or "").rstrip("/"),
            str(profile.get("chat_completions_path") or "/chat/completions"),
            str(profile.get("model") or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _endpoint(profile: dict[str, Any]) -> str:
    base = str(profile.get("base_url") or "").rstrip("/")
    path = str(profile.get("chat_completions_path") or "/chat/completions")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def run_probe(config: ConfigManager, kind: str, profile: dict[str, Any]) -> ApiProbeResult:
    """Make one real, minimal Chat Completions request.

    The model string is passed byte-for-byte from the profile. The API key is
    loaded only through ConfigManager's credential-store interface and is never
    returned or persisted in the result.
    """
    started = time.perf_counter()
    now = int(time.time() * 1000)
    signature = profile_signature(profile)
    model = str(profile.get("model") or "")
    status: int | None = None
    error = ""
    ok = False
    try:
        if not str(profile.get("base_url") or "").strip() or not model.strip():
            raise ValueError("缺少 Base URL 或模型名称")
        secret_id = str(profile.get("api_key_secret_id") or "")
        api_key = config.secret_get(secret_id)
        if not api_key:
            raise ValueError("系统凭据存储中没有 API Key")
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 4,
                "temperature": 0,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            _endpoint(profile),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "MaidAI-Control/0.3.0",
            },
        )
        timeout = min(60, max(5, int(profile.get("timeout_seconds") or 30)))
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
            raise ValueError("服务返回成功，但不是 Chat Completions 响应")
        ok = 200 <= status < 300
    except HTTPError as exc:
        status = int(exc.code)
        try:
            detail = exc.read(2048).decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        error = f"HTTP {exc.code}: {detail[:500]}".strip()
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        error = str(exc)[:500]
    latency = max(0, int((time.perf_counter() - started) * 1000))
    result = ApiProbeResult(ok, now, latency, status, "" if ok else error or "连接失败", signature, model)
    probes = config.data.setdefault("api_probes", {})
    probes[kind] = result.as_dict()
    config.save()
    return result


def probe_is_recent(
    config: ConfigManager,
    kind: str,
    profile: dict[str, Any] | None,
    *,
    max_age_ms: int = 24 * 60 * 60 * 1000,
) -> bool:
    probe = dict((config.data.get("api_probes") or {}).get(kind) or {})
    if not profile or not probe.get("last_probe_ok"):
        return False
    try:
        age = int(time.time() * 1000) - int(probe.get("last_probe_at") or 0)
    except (TypeError, ValueError):
        return False
    return (
        0 <= age <= max_age_ms
        and probe.get("profile_signature") == profile_signature(profile)
    )
