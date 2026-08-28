from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import floor
from maid_agent.building.models import Blueprint,BlueprintBlock
from maid_agent.protocol.models import StateSnapshot


@dataclass(frozen=True)
class PlannedBlock:
    index:int;x:int;y:int;z:int;item_id:str;block_id:str;face:str;optional:bool


class MaterialPlanner:
    def missing(self,blueprint:Blueprint,snapshot:StateSnapshot,*,completed_indices:set[int]|None=None)->dict[str,int]:
        completed_indices=completed_indices or set();need=Counter()
        for index,block in enumerate(blueprint.blocks):
            if index in completed_indices or block.optional:continue
            need[block.item_id]+=1
        have=Counter()
        for row in snapshot.inventory:have[row.id]+=row.count
        return {item:max(0,count-have.get(item,0)) for item,count in need.items() if count>have.get(item,0)}


class BuildPlanner:
    @staticmethod
    def rotate(x:int,z:int,rotation:int)->tuple[int,int]:
        r=rotation%360
        if r==0:return x,z
        if r==90:return -z,x
        if r==180:return -x,-z
        if r==270:return z,-x
        raise ValueError("rotation must be 0/90/180/270")

    def placements(self,blueprint:Blueprint,origin:dict[str,float],rotation:int)->list[PlannedBlock]:
        result=[]
        for index,block in enumerate(blueprint.blocks):
            rx,rz=self.rotate(block.x,block.z,rotation)
            result.append(PlannedBlock(index,floor(origin["x"])+rx,floor(origin["y"])+block.y,floor(origin["z"])+rz,block.item_id,block.block_id or block.item_id,block.face,block.optional))
        # Stable bottom-up order reduces unsupported floating placements.
        return sorted(result,key=lambda p:(p.y,p.index))

    def segments(self,blueprint:Blueprint,origin:dict[str,float],rotation:int)->list[list[PlannedBlock]]:
        rows=self.placements(blueprint,origin,rotation);size=blueprint.segment_size
        return [rows[i:i+size] for i in range(0,len(rows),size)]

    def remaining(
        self,
        blueprint:Blueprint,
        origin:dict[str,float],
        rotation:int,
        *,
        completed_indices:set[int],
        skipped_optional_indices:set[int]|None=None,
    )->list[PlannedBlock]:
        """Return sorted placements while keeping the Blueprint's original index as identity."""
        skipped_optional_indices=skipped_optional_indices or set()
        return [
            placement
            for placement in self.placements(blueprint,origin,rotation)
            if placement.index not in completed_indices
            and placement.index not in skipped_optional_indices
        ]
