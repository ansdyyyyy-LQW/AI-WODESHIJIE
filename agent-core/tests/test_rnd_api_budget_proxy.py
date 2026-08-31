from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from maid_agent.config import ProviderProfile, RndBudgetSettings, RuntimeBudgetSettings
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.api_budget_proxy import RndApiBudgetProxy
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.tokens.budget_guard import BudgetGuard
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


@pytest.mark.asyncio
async def test_proxy_records_dsh_usage_only_in_rnd_ledger_and_hard_stops_at_budget(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(
        store, tmp_path / "handoff", cycle_days=5, token_budget=100_000,
    ).create(5)
    ledger = TokenLedger(store)
    guard = BudgetGuard(
        ledger,
        RuntimeBudgetSettings(enabled=False),
        RndBudgetSettings(budget_per_cycle=100_000, max_single_request=100_000),
    )
    profile = ProviderProfile(
        profile_id="rnd-test", display_name="R&D Test",
        base_url="https://upstream.invalid/v1", model="configured-rnd-model",
        chat_completions_path="/chat/completions", max_retries=0,
    )
    upstream_requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        assert request.headers["authorization"] == "Bearer real-upstream-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "configured-rnd-model"
        assert payload["stream_options"]["include_usage"] is True
        chunk = {
            "id": "upstream-1", "object": "chat.completion.chunk",
            "model": "configured-rnd-model",
            "choices": [{
                "index": 0, "delta": {"content": "ok"}, "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 300, "completion_tokens": 80, "total_tokens": 380},
        }
        body = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    proxy = RndApiBudgetProxy(
        profile=profile, api_key="real-upstream-secret", ledger=ledger,
        budget_guard=guard, store=store, transport=httpx.MockTransport(upstream),
    )
    environment = await proxy.start(cycle_id=cycle.cycle_id, phase="DEVELOPMENT")
    assert environment["DEEPSEEK_API_KEY"] != "real-upstream-secret"
    assert environment["DEEPSEEK_BASE_URL"].startswith("http://127.0.0.1:")
    headers = {"authorization": f"Bearer {environment['DEEPSEEK_API_KEY']}"}
    payload = {
        "model": "dsh-default-model", "stream": True, "max_tokens": 1000,
        "messages": [{"role": "user", "content": "small DSH request"}],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                environment["DEEPSEEK_BASE_URL"] + "/chat/completions",
                headers=headers, json=payload,
            )
            assert response.status_code == 200 and "data: [DONE]" in response.text

            assert ledger.total("rnd", cycle_id=cycle.cycle_id) == 380
            assert ledger.total("runtime") == 0
            assert ledger.by_purpose("rnd", cycle_id=cycle.cycle_id) == {"rnd_development": 380}

            remaining_before_stop = 100_000 - ledger.total("rnd", cycle_id=cycle.cycle_id)
            ledger.record(
                ledger="rnd", purpose="test_fill", model="mock",
                request_id="fill-to-ten-left",
                usage=TokenUsage(remaining_before_stop - 10, 0, remaining_before_stop - 10),
                cycle_id=cycle.cycle_id,
            )
            blocked = await client.post(
                environment["DEEPSEEK_BASE_URL"] + "/chat/completions",
                headers=headers, json=payload,
            )
            assert blocked.status_code == 400
            assert blocked.json()["error"]["code"] in {
                "RND_CYCLE_BUDGET_EXCEEDED", "RND_FORCE_CLOSE",
            }
            assert len(upstream_requests) == 1
            assert ledger.total("runtime") == 0
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_proxy_forwards_locked_dsh_web_search_and_records_real_usage(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", cycle_days=5, token_budget=100_000).create(5)
    ledger = TokenLedger(store)
    guard = BudgetGuard(
        ledger, RuntimeBudgetSettings(enabled=False),
        RndBudgetSettings(budget_per_cycle=100_000, max_single_request=100_000),
    )
    profile = ProviderProfile(
        profile_id="rnd-search", display_name="R&D Search",
        base_url="https://upstream.invalid/v1", model="deepseek-v4-flash",
        chat_completions_path="/chat/completions", max_retries=0,
    )
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url) == "https://upstream.invalid/anthropic/v1/messages"
        assert request.headers["x-api-key"] == "real-search-secret"
        assert request.headers["authorization"] == "Bearer real-search-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["tools"][0]["type"] == "web_search_20250305"
        return httpx.Response(200, json={
            "content": [
                {"type": "web_search_tool_result", "content": []},
                {"type": "text", "text": "result", "citations": []},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 30},
        })

    proxy = RndApiBudgetProxy(
        profile=profile, api_key="real-search-secret", ledger=ledger,
        budget_guard=guard, store=store, transport=httpx.MockTransport(upstream),
    )
    environment = await proxy.start(cycle_id=cycle.cycle_id, phase="DEVELOPMENT")
    headers = {
        "authorization": f"Bearer {environment['DEEPSEEK_API_KEY']}",
        "x-api-key": environment["DEEPSEEK_API_KEY"],
        "anthropic-version": "2023-06-01",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                environment["DEEPSEEK_SEARCH_BASE_URL"] + "/messages",
                headers=headers,
                json={
                    "model": "deepseek-v4-flash", "max_tokens": 1000,
                    "messages": [{"role": "user", "content": "search"}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                },
            )
            assert response.status_code == 200
            assert ledger.by_purpose("rnd", cycle_id=cycle.cycle_id) == {"rnd_research": 150}
            blocked = await client.post(
                environment["DEEPSEEK_SEARCH_BASE_URL"] + "/unknown",
                headers=headers, json={"messages": []},
            )
            assert blocked.status_code == 400
            assert len(seen) == 1
            assert proxy.readiness()["web_research"] is True
    finally:
        await proxy.close()
