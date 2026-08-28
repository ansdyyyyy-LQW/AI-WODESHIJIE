from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DshMessage:
    type: str
    payload: dict[str, Any]

    @property
    def request_id(self) -> str:
        return str(self.payload.get("request_id") or "")


@dataclass(slots=True)
class DshRunResult:
    ok: bool
    code: str
    finish_reason: str
    summary: str
    session_id: str
    workspace: str
    phase: str
    usage: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def parse_dsh_message(line: bytes | str) -> DshMessage:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("DSH driver stdout contained non-JSON data") from exc
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("DSH driver message requires a string type")
    return DshMessage(str(value["type"]), value)
