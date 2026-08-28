from __future__ import annotations

import json

import httpx
import pytest

from maid_agent.config import ProviderProfile
from maid_agent.config import RndBudgetSettings, RuntimeBudgetSettings
from maid_agent.llm.openai_compatible import OpenAICompatibleProvider, estimate_tokens
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.tokens.budget_guard import BudgetGuard
from maid_agent.tokens.ledger import TokenLedger


@pytest.mark.asyncio
async def test_provider_preserves_model_and_path(tmp_path) -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "req-provider",
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
            },
        )

    store = MemoryStore(tmp_path / "state.sqlite3")
    ledger = TokenLedger(store)
    profile = ProviderProfile(
        profile_id="runtime",
        display_name="Runtime",
        base_url="https://relay.example/v1",
        model="Rim-3.1-channel-A",
        api_key_secret_id="runtime/key",
        chat_completions_path="/custom/chat/completions",
        max_retries=0,
    )
    provider = OpenAICompatibleProvider(
        profile,
        "secret",
        ledger,
        ledger_name="runtime",
        game_day_getter=lambda: 5,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.complete(
        [{"role": "user", "content": "Reply OK"}],
        model_role="runtime_strategy",
        purpose="planning",
    )
    assert result.content == "OK"
    assert captured["url"] == "https://relay.example/v1/custom/chat/completions"
    assert captured["body"]["model"] == "Rim-3.1-channel-A"
    assert ledger.total("runtime", game_day=5) == 8
    assert ledger.total("rnd") == 0


@pytest.mark.asyncio
async def test_rnd_request_uses_locked_remaining_budget_as_max_tokens(tmp_path) -> None:
    captured = {}
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "req-rnd", "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    store = MemoryStore(tmp_path / "state.sqlite3")
    cycle = RndTrigger(store, tmp_path / "handoff", token_budget=2000).create(5)
    with store.connection() as conn:
        conn.execute("UPDATE rnd_cycles SET status='RUNNING' WHERE cycle_id=?", (cycle.cycle_id,))
    ledger = TokenLedger(store)
    rnd_settings = RndBudgetSettings(budget_per_cycle=9_000_000, max_single_request=5_000)
    guard = BudgetGuard(ledger, RuntimeBudgetSettings(), rnd_settings)
    profile = ProviderProfile(profile_id="rnd", display_name="RND", base_url="https://relay.example/v1",
                              model="rnd-model", api_key_secret_id="rnd/key", max_retries=0)
    provider = OpenAICompatibleProvider(
        profile, "secret", ledger, ledger_name="rnd", budget_guard=guard,
        cycle_id_getter=lambda: cycle.cycle_id, transport=httpx.MockTransport(handler),
    )
    messages = [{"role": "user", "content": "Return OK"}]
    await provider.complete(messages, model_role="rnd_test", purpose="rnd_test")
    assert captured["body"]["max_tokens"] >= 1
    assert captured["body"]["max_tokens"] + estimate_tokens(messages) <= 2000
    rnd_settings.budget_per_cycle = 1
    assert guard.rnd_checkpoint(cycle_id=cycle.cycle_id).budget == 2000
