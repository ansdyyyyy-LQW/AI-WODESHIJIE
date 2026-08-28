from maid_agent.memory.store import MemoryStore
from maid_agent.skills.models import SkillSpec, SkillStep
from maid_agent.skills.store import SkillStore
from maid_agent.tokens.ledger import TokenLedger, TokenUsage


def test_runtime_and_rnd_ledgers_are_separate(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    ledger = TokenLedger(store)
    ledger.record(ledger="runtime", purpose="planning", model="runtime", request_id="r1", usage=TokenUsage(5, 2, 7), game_day=5)
    ledger.record(ledger="rnd", purpose="source_read", model="code", request_id="r2", usage=TokenUsage(10, 5, 15), game_day=5)
    assert ledger.total("runtime") == 7
    assert ledger.total("rnd") == 15
    assert ledger.snapshot(current_day=5, rnd_budget=100)["rnd_remaining"] == 85


def test_skill_version_and_statistics(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    skills = SkillStore(store)
    spec = SkillSpec(
        skill_id="logs-store",
        name="gather_logs_then_store",
        steps=[SkillStep(tool="find_visible_block", args={"query": "#minecraft:logs"})],
    )
    skills.put(spec)
    skills.record(spec.skill_id, 1, success=False, duration=3.0, code="PATH_NOT_FOUND")
    skills.record(spec.skill_id, 1, success=True, duration=1.0, code="OK")
    row = skills.list(status=None)[0]
    assert row["success_count"] == 1
    assert row["failure_count"] == 1
    assert row["failure_codes"] == {"PATH_NOT_FOUND": 1}
    assert row["avg_duration"] == 2.0
    assert skills.get("logs-store").name == "gather_logs_then_store"
