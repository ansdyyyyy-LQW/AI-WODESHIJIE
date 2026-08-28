from __future__ import annotations

import hashlib
import json
import time
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(StrEnum):
    HELLO="HELLO"; PING="PING"; PONG="PONG"; STATE_SNAPSHOT="STATE_SNAPSHOT"; STATE_RESYNC="STATE_RESYNC"
    EVENT="EVENT"; ACTION_REQUEST="ACTION_REQUEST"; ACTION_ACK="ACTION_ACK"; ACTION_RESULT="ACTION_RESULT"
    CONTROL_REQUEST="CONTROL_REQUEST"; CONTROL_RESULT="CONTROL_RESULT"; DISCOVER_MAIDS="DISCOVER_MAIDS"
    MAID_LIST="MAID_LIST"; LIST_PLAYERS="LIST_PLAYERS"; PLAYER_LIST="PLAYER_LIST"
    BIND_MAID="BIND_MAID"; UNBIND_MAID="UNBIND_MAID"; SAFE_IDLE="SAFE_IDLE"
    RND_STATUS="RND_STATUS"


class ActionStatus(StrEnum):
    SUCCESS="SUCCESS"; FAILED="FAILED"; CANCELLED="CANCELLED"; PREEMPTED="PREEMPTED"; TIMEOUT="TIMEOUT"


class ProtocolEnvelope(BaseModel):
    model_config=ConfigDict(extra="allow")
    protocol_version:int=1
    type:MessageType|str
    session_id:str=""
    message_id:str=Field(default_factory=lambda:str(uuid4()))
    maid_uuid:str|None=None
    game_tick:int=0
    timestamp_ms:int=Field(default_factory=lambda:int(time.time()*1000))
    payload:dict[str,Any]=Field(default_factory=dict)

    @classmethod
    def make(cls,message_type:MessageType|str,payload:dict[str,Any]|None=None,**kwargs:Any)->"ProtocolEnvelope":
        return cls(type=message_type,payload=payload or {},**kwargs)


class Position(BaseModel):
    x:float; y:float; z:float

    def as_dict(self)->dict[str,float]: return self.model_dump()


class InventoryEntry(BaseModel):
    slot:int; id:str; count:int; damage:int=0


class NearbyEntity(BaseModel):
    uuid:str; type:str; category:str; distance:float
    relative:dict[str,float]=Field(default_factory=dict)
    health:float|None=None; line_of_sight:bool=False; targeting_maid:bool=False
    recent_attacker:bool=False


class StateSnapshot(BaseModel):
    model_config=ConfigDict(extra="allow")
    dimension:str; day:int; time_of_day:int; game_tick:int=0; position:Position
    yaw:float=0; pitch:float=0; health:float; max_health:float; hunger:int|None=None
    air:int=300; on_fire:bool=False; in_water:bool=False; weather:str="CLEAR"; biome:str="unknown"
    inventory:list[InventoryEntry]=Field(default_factory=list)
    nearby_entities:list[NearbyEntity]=Field(default_factory=list)
    entity_presence:dict[str,str]=Field(default_factory=dict)
    visible_blocks:list[dict[str,Any]]=Field(default_factory=list)
    main_hand_item:str="minecraft:air"; off_hand_item:str="minecraft:air"
    current_action:dict[str,Any]|None=None; current_goal_id:str|None=None
    navigation:dict[str,Any]=Field(default_factory=dict); navigation_in_progress:bool=False
    reflex_state:str="NONE"; owner_uuid:str|None=None; owner_name:str|None=None

    def item_count(self,item_id:str)->int:
        return sum(row.count for row in self.inventory if row.id==item_id)

    def matching_item_count(self,predicate:str)->int:
        if predicate=="#minecraft:logs":
            return sum(row.count for row in self.inventory if row.id.endswith(("_log","_wood","_stem","_hyphae")))
        if predicate=="#minecraft:planks":
            return sum(row.count for row in self.inventory if row.id.endswith("_planks"))
        if predicate=="food":
            tokens=("bread","apple","carrot","potato","beef","porkchop","chicken","mutton","rabbit","cod","salmon","melon_slice","sweet_berries","cookie","stew")
            return sum(row.count for row in self.inventory if any(token in row.id for token in tokens))
        return self.item_count(predicate)

    def nearest_hostile(self)->NearbyEntity|None:
        rows=[e for e in self.nearby_entities if e.category in {"HOSTILE","MONSTER","ENEMY"}]
        return min(rows,key=lambda e:e.distance,default=None)

    def inventory_hash(self)->str:
        rows=sorted((r.id,r.count,r.damage) for r in self.inventory)
        return hashlib.sha256(json.dumps(rows,separators=(",",":")).encode()).hexdigest()


class BridgeEvent(BaseModel):
    event_id:str
    event_type:str
    severity:Literal["DEBUG","INFO","WARN","ERROR","CRITICAL"]="INFO"
    position:Position|None=None
    data:dict[str,Any]=Field(default_factory=dict)
    game_day:int|None=None
    time_of_day:int|None=None
    period:Literal["DAY","NIGHT"]|None=None
    game_tick:int=0
    maid_uuid:str|None=None

    @model_validator(mode="after")
    def ensure_id(self)->"BridgeEvent":
        if not self.event_id.strip():
            raw=f"{self.event_type}|{self.game_tick}|{self.maid_uuid}|{json.dumps(self.data,sort_keys=True,default=str)}"
            self.event_id=hashlib.sha256(raw.encode()).hexdigest()[:32]
        return self


class ActionRequest(BaseModel):
    request_id:str=Field(default_factory=lambda:str(uuid4()))
    action:str; args:dict[str,Any]=Field(default_factory=dict)
    timeout_ticks:int=Field(1200,ge=1,le=72000)


class ActionResult(BaseModel):
    request_id:str; action_id:str; status:ActionStatus; code:str
    data:dict[str,Any]=Field(default_factory=dict); world_delta:dict[str,Any]=Field(default_factory=dict)

    @property
    def ok(self)->bool: return self.status==ActionStatus.SUCCESS


class ControlRequest(BaseModel):
    request_id:str=Field(default_factory=lambda:str(uuid4())); command:str; args:dict[str,Any]=Field(default_factory=dict); token:str=""


class ControlResult(BaseModel):
    request_id:str; ok:bool; code:str="OK"; data:dict[str,Any]=Field(default_factory=dict); message:str=""
