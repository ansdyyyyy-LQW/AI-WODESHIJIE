from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    safe = name.removesuffix(".md")
    if not safe.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid prompt name")
    filename = f"{safe}.md"
    # Production prompts have one packaged source of truth. Missing resources fail
    # closed instead of falling back to a possibly stale repository copy.
    text = files("maid_agent.prompts").joinpath(filename).read_text(encoding="utf-8")
    text = text.strip()
    if not text:
        raise RuntimeError(f"prompt is empty: {filename}")
    return text
