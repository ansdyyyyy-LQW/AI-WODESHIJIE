from __future__ import annotations

from maid_agent.building.executor import BlueprintExecutor
from maid_agent.goal.models import Condition
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import (
    ActionResult,
    ActionStatus,
    InventoryEntry,
    Position,
    StateSnapshot,
)


class RecordingClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def execute(self, _action: str, args: dict, **kwargs: object) -> ActionResult:
        self.requests.append(args)
        completed = [row["index"] for row in args["placements"]]
        request_id = str(kwargs["request_id"])
        return ActionResult(
            request_id=request_id,
            action_id=request_id,
            status=ActionStatus.SUCCESS,
            code="BUILD_CHUNK_COMPLETE",
            data={"completed_indices": completed},
        )


class OptionalRaceClient(RecordingClient):
    async def execute(self, _action: str, args: dict, **kwargs: object) -> ActionResult:
        self.requests.append(args)
        request_id = str(kwargs["request_id"])
        if len(self.requests) == 1:
            return ActionResult(
                request_id=request_id,
                action_id=request_id,
                status=ActionStatus.FAILED,
                code="NO_MATERIAL",
                data={"completed_indices": [1], "failed_index": 2, "missing_materials": {"minecraft:gold_block": 1}},
            )
        return ActionResult(
            request_id=request_id,
            action_id=request_id,
            status=ActionStatus.SUCCESS,
            code="BUILD_CHUNK_COMPLETE",
            data={"completed_indices": [row["index"] for row in args["placements"]]},
        )


class PartialOnceClient(RecordingClient):
    async def execute(self, _action: str, args: dict, **kwargs: object) -> ActionResult:
        self.requests.append(args)
        request_id = str(kwargs["request_id"])
        rows = args["placements"]
        return ActionResult(
            request_id=request_id,
            action_id=request_id,
            status=ActionStatus.FAILED,
            code="NO_MATERIAL",
            data={
                "completed_indices": [row["index"] for row in rows[:-1]],
                "failed_index": rows[-1]["index"],
                "missing_materials": {"minecraft:stone": 1},
            },
        )


def build_snapshot() -> StateSnapshot:
    return StateSnapshot(
        dimension="minecraft:overworld",
        day=1,
        time_of_day=1000,
        game_tick=20,
        position=Position(x=0, y=64, z=0),
        health=20,
        max_health=20,
        inventory=[
            InventoryEntry(slot=0, id="minecraft:stone", count=1),
            InventoryEntry(slot=1, id="minecraft:dirt", count=1),
        ],
    )


async def test_blueprint_uses_original_indices_and_skips_missing_optional(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    client = RecordingClient()
    executor = BlueprintExecutor(client, store, segment_size=8)
    blueprint = {
        "blueprint_id": "index-order",
        "name": "index order",
        "blocks": [
            {"x": 0, "y": 1, "z": 0, "item_id": "minecraft:stone"},
            {"x": 0, "y": 0, "z": 0, "item_id": "minecraft:dirt"},
            {
                "x": 1,
                "y": 0,
                "z": 0,
                "item_id": "minecraft:gold_block",
                "optional": True,
            },
        ],
    }

    result = await executor.execute(
        blueprint,
        {"x": 10, "y": 64, "z": 10},
        0,
        build_snapshot,
    )

    assert result.ok and result.code == "BUILD_COMPLETE"
    assert result.data["complete"] is True
    assert [row["index"] for row in client.requests[0]["placements"]] == [1, 0]
    assert result.data["completed_indices"] == [0, 1]
    assert result.data["skipped_optional_indices"] == [2]
    assert PostconditionVerifier().evaluate(
        Condition(type="CUSTOM", args={"predicate": "build_complete"}),
        build_snapshot(),
        result,
    )

    checkpoint = store.list_build_checkpoints()[0]
    assert checkpoint["completed_indices"] == [0, 1]
    assert checkpoint["skipped_optional_indices"] == [2]


async def test_optional_material_race_never_blocks_later_required_blocks(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    client = OptionalRaceClient()
    executor = BlueprintExecutor(client, store, segment_size=8)
    blueprint = {
        "blueprint_id": "optional-race",
        "name": "optional race",
        "blocks": [
            {"x": 0, "y": 1, "z": 0, "item_id": "minecraft:stone"},
            {"x": 0, "y": 0, "z": 0, "item_id": "minecraft:dirt"},
            {"x": 1, "y": 0, "z": 0, "item_id": "minecraft:gold_block", "optional": True},
        ],
    }
    snapshot = build_snapshot().model_copy(
        update={"inventory": [*build_snapshot().inventory, InventoryEntry(slot=2, id="minecraft:gold_block", count=1)]}
    )
    result = await executor.execute(blueprint, {"x": 0, "y": 64, "z": 0}, 0, lambda: snapshot)
    assert result.ok
    assert result.data["completed_indices"] == [0, 1]
    assert result.data["skipped_optional_indices"] == [2]
    assert [row["index"] for row in client.requests[1]["placements"]] == [0]


async def test_bridge_can_reconcile_already_placed_block_without_inventory(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    client = RecordingClient()
    executor = BlueprintExecutor(client, store)
    empty = build_snapshot().model_copy(update={"inventory": []})
    result = await executor.execute(
        {"blueprint_id": "already-there", "name": "already there", "blocks": [{"x": 0, "y": 0, "z": 0, "item_id": "minecraft:stone"}]},
        {"x": 0, "y": 64, "z": 0},
        0,
        lambda: empty,
    )
    assert result.ok and result.data["completed_indices"] == [0]
    assert len(client.requests) == 1


async def test_three_by_three_by_two_resumes_original_checkpoint(tmp_path) -> None:
    store = MemoryStore(tmp_path / "state.sqlite3")
    blocks = [
        {"x": x, "y": y, "z": z, "item_id": "minecraft:stone"}
        for y in range(2) for x in range(3) for z in range(3)
    ]
    blueprint = {"blueprint_id": "3x3x2", "name": "3x3x2", "segment_size": 7, "blocks": blocks}
    snapshot = build_snapshot().model_copy(update={"inventory": [InventoryEntry(slot=0, id="minecraft:stone", count=18)]})
    partial_client = PartialOnceClient()
    first = await BlueprintExecutor(partial_client, store).execute(
        blueprint, {"x": 0, "y": 64, "z": 0}, 0, lambda: snapshot
    )
    assert first.code == "NO_MATERIAL" and first.data["completed_indices"] == [0, 1, 2, 3, 4, 5]

    resumed_client = RecordingClient()
    resumed = await BlueprintExecutor(resumed_client, store).execute(
        blueprint, {"x": 0, "y": 64, "z": 0}, 0, lambda: snapshot
    )
    sent = [row["index"] for request in resumed_client.requests for row in request["placements"]]
    assert resumed.ok and resumed.data["completed_indices"] == list(range(18))
    assert sent == list(range(6, 18))
