from __future__ import annotations

from collections import Counter
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from maid_agent.actions.client import ActionClient
from maid_agent.building.models import Blueprint, BuildCheckpoint
from maid_agent.building.planner import BuildPlanner, MaterialPlanner
from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import ActionResult, ActionStatus, StateSnapshot


class BlueprintExecutor:
    """Runs a blueprint through the real Bridge body and persists exact block indices.

    The executor never marks a block complete merely because a segment number advanced.
    Every completed index is either already the expected block in the world (reported by
    Bridge) or was actually placed by EntityMaid.
    """

    def __init__(self, client: ActionClient, store: MemoryStore, *, segment_size: int = 64):
        self.client = client
        self.store = store
        self.segment_size = max(1, min(128, int(segment_size)))
        self.materials = MaterialPlanner()
        self.planner = BuildPlanner()

    async def execute(
        self,
        blueprint_data: dict,
        origin: dict,
        rotation: int,
        snapshot_getter: Callable[[], StateSnapshot | None],
    ) -> ActionResult:
        blueprint = Blueprint.model_validate(blueprint_data)
        build_id = str(
            uuid5(
                NAMESPACE_URL,
                f"maid-build:{blueprint.blueprint_id}:{origin['x']}:{origin['y']}:{origin['z']}:{rotation}",
            )
        )
        restored = self.store.load_build_checkpoint(build_id)
        valid_indices=set(range(len(blueprint.blocks)))
        completed_indices = {
            int(value) for value in (restored or {}).get("completed_indices", [])
            if int(value) in valid_indices
        }
        skipped_optional_indices = {
            int(value)
            for value in (restored or {}).get("skipped_optional_indices", [])
            if int(value) in valid_indices and blueprint.blocks[int(value)].optional
        }
        checkpoint = BuildCheckpoint(
            build_id=build_id,
            blueprint_id=blueprint.blueprint_id,
            origin=origin,
            rotation=rotation,
            next_segment=int((restored or {}).get("next_segment", 0)),
            completed_blocks=len(completed_indices),
            completed_indices=sorted(completed_indices),
            skipped_optional_indices=sorted(skipped_optional_indices),
            status=str((restored or {}).get("status", "RUNNING")),
            missing_items=dict((restored or {}).get("missing_items", {})),
            last_error=str((restored or {}).get("last_error", "")),
        )
        placements = self.planner.placements(blueprint, origin, rotation)
        required_indices={placement.index for placement in placements if not placement.optional}
        if required_indices.issubset(completed_indices) and checkpoint.status=="DONE":
            checkpoint.status = "DONE"
            self._save(checkpoint, blueprint)
            return self._result(build_id, ActionStatus.SUCCESS, "BUILD_COMPLETE", checkpoint)

        snapshot = snapshot_getter()
        if snapshot is None:
            checkpoint.status = "PAUSED"
            checkpoint.last_error = "NO_SNAPSHOT"
            self._save(checkpoint, blueprint)
            return self._result(build_id, ActionStatus.FAILED, "NO_SNAPSHOT", checkpoint)

        # Required material is reserved first. Optional placements consume only the
        # remaining surplus so an optional decoration can never starve the build.
        have=Counter()
        for row in snapshot.inventory:
            have[row.id]+=row.count
        required_remaining=Counter(
            placement.item_id
            for placement in placements
            if not placement.optional and placement.index not in completed_indices
        )
        optional_budget=have-required_remaining
        runnable=[]
        for placement in self.planner.remaining(
            blueprint,
            origin,
            rotation,
            completed_indices=completed_indices,
            skipped_optional_indices=skipped_optional_indices,
        ):
            if placement.optional:
                if optional_budget[placement.item_id]<=0:
                    skipped_optional_indices.add(placement.index)
                    continue
                optional_budget[placement.item_id]-=1
            runnable.append(placement)
        checkpoint.skipped_optional_indices=sorted(skipped_optional_indices)

        if not runnable:
            checkpoint.status="DONE"
            checkpoint.last_error=""
            self._save(checkpoint,blueprint)
            return self._result(build_id,ActionStatus.SUCCESS,"BUILD_COMPLETE",checkpoint)

        segment_size=min(self.segment_size,blueprint.segment_size)
        while runnable:
            segment=runnable[:segment_size]
            segment_number=checkpoint.next_segment
            payload = [
                {
                    "index": placement.index,
                    "x": placement.x,
                    "y": placement.y,
                    "z": placement.z,
                    "item_id": placement.item_id,
                    "face": placement.face,
                }
                for placement in segment
            ]
            request_id = str(uuid5(NAMESPACE_URL, f"build:{build_id}:segment:{segment_number}"))
            result = await self.client.execute(
                "build_chunk",
                {"placements": payload, "allow_partial": True},
                timeout_ticks=max(1200, len(payload) * 240),
                request_id=request_id,
            )
            reported = result.data.get("completed_indices") or result.data.get("placed_indices") or []
            before_completed=len(completed_indices)
            for value in reported:
                try:
                    completed_indices.add(int(value))
                except (TypeError, ValueError):
                    continue
            checkpoint.completed_indices = sorted(completed_indices)
            checkpoint.completed_blocks = len(completed_indices)
            checkpoint.next_segment = segment_number + 1
            checkpoint.missing_items = {}
            checkpoint.last_error = "" if result.ok else result.code
            checkpoint.status = "RUNNING" if result.ok else "PAUSED"
            self._save(checkpoint, blueprint)
            runnable=[
                placement for placement in runnable
                if placement.index not in completed_indices
                and placement.index not in skipped_optional_indices
            ]
            if result.ok:
                if len(completed_indices)==before_completed and segment:
                    checkpoint.status="PAUSED"
                    checkpoint.last_error="BUILD_POSTCONDITION_FAILED"
                    self._save(checkpoint,blueprint)
                    return self._result(build_id,ActionStatus.FAILED,"BUILD_POSTCONDITION_FAILED",checkpoint)
                continue
            if not result.ok:
                failed_index=result.data.get("failed_index")
                try:failed_index=int(failed_index)
                except (TypeError,ValueError):failed_index=-1
                if (
                    result.code=="NO_MATERIAL"
                    and failed_index in valid_indices
                    and blueprint.blocks[failed_index].optional
                ):
                    failed_item=blueprint.blocks[failed_index].item_id
                    skipped_optional_indices.update(
                        placement.index for placement in runnable
                        if placement.optional and placement.item_id==failed_item
                    )
                    checkpoint.skipped_optional_indices=sorted(skipped_optional_indices)
                    checkpoint.status="RUNNING"
                    checkpoint.last_error=""
                    runnable=[
                        placement for placement in runnable
                        if placement.index not in skipped_optional_indices
                    ]
                    self._save(checkpoint,blueprint)
                    continue
                # Recalculate from the newest real inventory so Runtime can create a
                # precise child material Goal instead of guessing from the error text.
                latest = snapshot_getter()
                missing_now = (
                    self.materials.missing(
                        blueprint, latest, completed_indices=completed_indices
                    )
                    if latest is not None
                    else {}
                )
                if result.code == "NO_MATERIAL" or missing_now:
                    checkpoint.status = "PAUSED_MATERIALS"
                    checkpoint.missing_items = missing_now or dict(
                        result.data.get("missing_materials") or {}
                    )
                    checkpoint.last_error = "NO_MATERIAL"
                    self._save(checkpoint, blueprint)
                    return self._result(
                        build_id, ActionStatus.FAILED, "NO_MATERIAL", checkpoint
                    )
                return self._result(
                    build_id, ActionStatus.FAILED, result.code, checkpoint
                )

        if not required_indices.issubset(completed_indices):
            checkpoint.status="PAUSED"
            checkpoint.last_error="BUILD_POSTCONDITION_FAILED"
            self._save(checkpoint,blueprint)
            return self._result(build_id,ActionStatus.FAILED,"BUILD_POSTCONDITION_FAILED",checkpoint)
        checkpoint.status = "DONE"
        checkpoint.completed_indices = sorted(completed_indices)
        checkpoint.completed_blocks = len(completed_indices)
        checkpoint.last_error = ""
        self._save(checkpoint, blueprint)
        return self._result(build_id, ActionStatus.SUCCESS, "BUILD_COMPLETE", checkpoint)

    def _save(self, checkpoint: BuildCheckpoint, blueprint: Blueprint) -> None:
        self.store.save_build_checkpoint(
            build_id=checkpoint.build_id,
            blueprint=blueprint.model_dump(mode="json"),
            origin=checkpoint.origin,
            rotation=checkpoint.rotation,
            next_segment=checkpoint.next_segment,
            completed_blocks=checkpoint.completed_blocks,
            completed_indices=checkpoint.completed_indices,
            skipped_optional_indices=checkpoint.skipped_optional_indices,
            status=checkpoint.status,
            missing_items=checkpoint.missing_items,
            last_error=checkpoint.last_error,
        )

    @staticmethod
    def _result(
        build_id: str,
        status: ActionStatus,
        code: str,
        checkpoint: BuildCheckpoint,
    ) -> ActionResult:
        return ActionResult(
            request_id=build_id,
            action_id=build_id,
            status=status,
            code=code,
            data={
                "build_id": build_id,
                "complete": status==ActionStatus.SUCCESS and code=="BUILD_COMPLETE" and checkpoint.status=="DONE",
                "checkpoint": checkpoint.model_dump(mode="json"),
                "missing_materials": checkpoint.missing_items,
                "completed_indices": checkpoint.completed_indices,
                "skipped_optional_indices": checkpoint.skipped_optional_indices,
            },
            world_delta={"completed_blocks": checkpoint.completed_blocks},
        )
