from __future__ import annotations

import json
import re
from typing import Any


_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
_INTERNAL_FIELD = re.compile(
    r"(?i)\b(?:goal|plan|step|request|action|cycle|project)_id\s*[:=]\s*[^\s,;，；]+"
)
_ENUM = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_SPACE = re.compile(r"\s+")

_REPLACEMENTS = (
    ("R&D Harness", "本地研发环境"), ("Source Workspace", "研发源码目录"),
    ("Start Gate", "启动条件"), ("Postcondition", "结果确认"),
    ("Checkpoint", "进度保存点"), ("Token Ledger", "用量记录"),
    ("ThreatAnalytics", "危险分析"), ("Memory Context", "记忆信息"),
    ("PlanStep", "任务步骤"), ("Bridge", "游戏连接"), ("Runtime", "日常 AI"),
    ("Goal", "目标"), ("Plan", "任务计划"), ("Action", "操作"),
    ("Runner", "本地研发程序"), ("Worktree", "隔离源码目录"),
    ("Candidate", "待确认成果"), ("Patch", "修改内容"),
    ("UUID", "内部编号"), ("JSON", "结构化数据"), ("Protocol", "连接规则"),
)


def _extract(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("summary", "decision_summary", "objective", "description", "reason", "message"):
            if value.get(key):
                return _extract(value[key])
        return ""
    if isinstance(value, list):
        return "；".join(filter(None, (_extract(item) for item in value[:3])))
    return str(value or "")


def public_model_text(value: Any, fallback: str = "", *, max_length: int = 500) -> str:
    """Small public-UI filter; technical views continue to use their raw data."""
    text = _extract(value).strip()
    if text[:1] in {"{", "["}:
        try:
            text = _extract(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return fallback
    for source, target in _REPLACEMENTS:
        text = text.replace(source, target)
    text = _INTERNAL_FIELD.sub("", text)
    text = _UUID.sub("内部编号", text)
    text = _ENUM.sub("内部状态", text)
    text = _SPACE.sub(" ", text).strip(" ,;，；:：")
    if not text:
        return fallback
    if len(text) > max_length:
        text = text[: max(1, max_length - 1)].rstrip() + "…"
    return text
