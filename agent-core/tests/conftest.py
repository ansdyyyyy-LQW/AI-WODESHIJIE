from __future__ import annotations

import pytest

from maid_agent.protocol.models import InventoryEntry, Position, StateSnapshot


@pytest.fixture
def snapshot() -> StateSnapshot:
    return StateSnapshot(
        dimension="minecraft:overworld",
        day=5,
        time_of_day=6000,
        game_tick=12000,
        position=Position(x=1, y=64, z=2),
        health=18,
        max_health=20,
        hunger=16,
        inventory=[
            InventoryEntry(slot=0, id="minecraft:oak_log", count=6),
            InventoryEntry(slot=1, id="minecraft:bread", count=3),
        ],
    )
