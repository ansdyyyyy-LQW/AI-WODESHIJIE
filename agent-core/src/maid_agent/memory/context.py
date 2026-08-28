from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import BridgeEvent, StateSnapshot


RESOURCE_MARKERS=("_ore","_log","_stem","coal_ore","iron_ore","copper_ore","diamond_ore","ancient_debris")
STRUCTURE_KINDS={
    "minecraft:crafting_table":"crafting_table",
    "minecraft:furnace":"furnace",
    "minecraft:blast_furnace":"blast_furnace",
    "minecraft:smoker":"smoker",
    "minecraft:chest":"container",
    "minecraft:trapped_chest":"container",
    "minecraft:barrel":"container",
}


@dataclass
class MemoryIngestor:
    store:MemoryStore
    _last_inventory:dict[str,int]=field(default_factory=dict)
    _last_day:int=-1
    _last_location_tick:int=-10_000

    def ingest_snapshot(self,snapshot:StateSnapshot)->list[dict[str,Any]]:
        generated=[]
        if snapshot.game_tick-self._last_location_tick>=200:
            self.store.remember_location(
                name=f"已访问区域 Day {snapshot.day}",dimension=snapshot.dimension,
                x=snapshot.position.x,y=snapshot.position.y,z=snapshot.position.z,
                tags=["visited",snapshot.biome],confidence=.7,game_tick=snapshot.game_tick,game_day=snapshot.day,
                metadata={"biome":snapshot.biome,"weather":snapshot.weather},
            )
            self._last_location_tick=snapshot.game_tick
        for block in snapshot.visible_blocks:
            block_id=str(block.get("id", ""));
            try:x=int(block["x"]);y=int(block["y"]);z=int(block["z"])
            except (KeyError,TypeError,ValueError):continue
            if any(marker in block_id for marker in RESOURCE_MARKERS):
                self.store.upsert_resource_observation(
                    block_id=block_id,dimension=snapshot.dimension,x=x,y=y,z=z,
                    exposed=bool(block.get("exposed",True)),game_tick=snapshot.game_tick,game_day=snapshot.day,
                    metadata={"state":block.get("state",{})},
                )
            kind=STRUCTURE_KINDS.get(block_id)
            if kind:
                self.store.remember_structure(
                    kind=kind,name=block_id.split(":")[-1],dimension=snapshot.dimension,
                    x=x,y=y,z=z,tags=["workstation" if kind not in {"container"} else "storage"],
                    game_tick=snapshot.game_tick,metadata={"block_id":block_id,"state":block.get("state",{})},
                )
        current:dict[str,int]={}
        for row in snapshot.inventory:current[row.id]=current.get(row.id,0)+row.count
        if self._last_inventory:
            for item_id in sorted(set(current)|set(self._last_inventory)):
                delta=current.get(item_id,0)-self._last_inventory.get(item_id,0)
                if delta:
                    event_type="ITEM_ACQUIRED" if delta>0 else "ITEM_CONSUMED"
                    key=f"snapshot:{snapshot.game_tick}:{event_type}:{item_id}:{delta}"
                    self.store.record_event(
                        game_day=snapshot.day,game_tick=snapshot.game_tick,event_type=event_type,
                        severity="INFO",payload={"item_id":item_id,"delta":delta,"source":"snapshot_delta"},
                        position=snapshot.position.model_dump(),event_key=hashlib.sha256(key.encode()).hexdigest(),source="agent",
                    )
                    generated.append({"event_type":event_type,"data":{"item_id":item_id,"delta":delta}})
        self._last_inventory=current
        self._last_day=snapshot.day
        return generated

    def ingest_event(self,event:BridgeEvent,*,fallback_day:int)->bool:
        _,inserted=self.store.record_bridge_event(event,fallback_day=fallback_day)
        if not inserted:return False
        if event.event_type=="BLOCK_BROKEN":
            data=event.data
            if data.get("resource_id"):self.store.mark_resource_exhausted(resource_id=str(data["resource_id"]))
            elif all(key in data for key in ("x","y","z")):self.store.mark_resource_exhausted(dimension=str(data.get("dimension") or ""),x=int(data["x"]),y=int(data["y"]),z=int(data["z"]))
        if event.event_type in {"BASE_ESTABLISHED","SHELTER_COMPLETED"} and event.position:
            self.store.remember_location(
                name=str(event.data.get("name") or "安全基地"),dimension=str(event.data.get("dimension") or "minecraft:overworld"),
                x=event.position.x,y=event.position.y,z=event.position.z,tags=["safe","base","shelter"],
                confidence=1.0,game_tick=event.game_tick,game_day=event.game_day or fallback_day,metadata=event.data,
            )
        return True
