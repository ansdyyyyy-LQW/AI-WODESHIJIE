from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import uuid4
from pydantic import BaseModel,Field,model_validator


class BlueprintBlock(BaseModel):
    x:int;y:int;z:int
    item_id:str
    block_id:str|None=None
    face:str="UP"
    optional:bool=False


class Blueprint(BaseModel):
    blueprint_id:str=Field(default_factory=lambda:str(uuid4()))
    name:str
    version:int=Field(1,ge=1)
    blocks:list[BlueprintBlock]
    segment_size:int=Field(24,ge=1,le=128)
    metadata:dict[str,Any]=Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_positions(self)->"Blueprint":
        seen=set()
        for block in self.blocks:
            pos=(block.x,block.y,block.z)
            if pos in seen:raise ValueError(f"duplicate blueprint position: {pos}")
            seen.add(pos)
        if len(self.blocks)>100_000:raise ValueError("blueprint exceeds 100000 blocks")
        return self

    def material_bill(self)->dict[str,int]:
        return dict(Counter(block.item_id for block in self.blocks if not block.optional))


class BuildCheckpoint(BaseModel):
    build_id:str
    blueprint_id:str
    origin:dict[str,float]
    rotation:int=0
    next_segment:int=0
    completed_blocks:int=0
    completed_indices:list[int]=Field(default_factory=list)
    skipped_optional_indices:list[int]=Field(default_factory=list)
    status:str="READY"
    missing_items:dict[str,int]=Field(default_factory=dict)
    last_error:str=""
