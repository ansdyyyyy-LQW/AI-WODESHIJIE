from __future__ import annotations

from math import dist

from maid_agent.goal.models import Condition
from maid_agent.protocol.models import ActionResult,StateSnapshot


class PostconditionVerifier:
    HOSTILE_CATEGORIES={"HOSTILE","MONSTER","ENEMY"}

    def evaluate(self,condition:Condition,snapshot:StateSnapshot,action_result:ActionResult|None=None)->bool:
        a=condition.args
        match condition.type:
            case "ITEM_COUNT":return snapshot.item_count(str(a["item_id"]))>=int(a.get("count",1))
            case "TAG_COUNT":return snapshot.matching_item_count(str(a["tag"]))>=int(a.get("count",1))
            case "HEALTH_AT_LEAST":return snapshot.health>=float(a["value"])
            case "HEALTH_BELOW":return snapshot.health<float(a["value"])
            case "HUNGER_AT_LEAST":return snapshot.hunger is not None and snapshot.hunger>=int(a["value"])
            case "POSITION_WITHIN":
                return dist((snapshot.position.x,snapshot.position.y,snapshot.position.z),(float(a["x"]),float(a["y"]),float(a["z"])))<=float(a.get("range",1.5))
            case "NO_HOSTILE_WITHIN":
                radius=float(a.get("radius",16));return not any(e.category in self.HOSTILE_CATEGORIES and e.distance<=radius for e in snapshot.nearby_entities)
            case "ENTITY_EXISTS":
                state=snapshot.entity_presence.get(str(a["uuid"]),"")
                return state in {"OBSERVABLE","PRESENT_NOT_OBSERVABLE"} or any(e.uuid==str(a["uuid"]) for e in snapshot.nearby_entities)
            case "ENTITY_GONE":
                return snapshot.entity_presence.get(str(a["uuid"]),"") in {"DEAD","CONFIRMED_GONE"}
            case "ENTITY_DISTANCE_AT_MOST":
                return any(e.uuid==str(a["uuid"]) and e.distance<=float(a["distance"]) for e in snapshot.nearby_entities)
            case "ENTITY_DISTANCE_AT_LEAST":
                row=next((e for e in snapshot.nearby_entities if e.uuid==str(a["uuid"])),None)
                return row is not None and row.distance>=float(a["distance"])
            case "ENTITY_TARGETING_MAID":
                row=next((e for e in snapshot.nearby_entities if e.uuid==str(a["uuid"])),None)
                return row is not None and row.targeting_maid is bool(a.get("equals",True))
            case "ACTION_CODE":return action_result is not None and action_result.code==str(a["code"])
            case "ACTION_STATUS":return action_result is not None and str(action_result.status)==str(a["status"])
            case "WORLD_DELTA":return action_result is not None and action_result.world_delta.get(str(a["key"]))==a.get("equals",True)
            case "INVENTORY_DELTA":
                if action_result is None:return False
                delta=action_result.data.get("inventory_delta") or action_result.world_delta.get("inventory_delta") or {}
                return int(delta.get(str(a["item_id"]),0))>=int(a.get("at_least",1))
            case "BLOCK_STATE":
                target=(int(a["x"]),int(a["y"]),int(a["z"]));expected=str(a.get("block_id","minecraft:air"))
                for row in snapshot.visible_blocks:
                    if (int(row.get("x",2**31)),int(row.get("y",2**31)),int(row.get("z",2**31)))==target:
                        return str(row.get("id"))==expected
                return expected=="minecraft:air" and action_result is not None and bool(action_result.world_delta.get("block_broken"))
            case "BLOCK_AIR":
                if action_result is not None and action_result.data.get("visible") is True:
                    return action_result.data.get("air") is bool(a.get("equals",True))
                return False
            case "LOCAL_SPACE":
                if action_result is None:return False
                return action_result.data.get(str(a["key"])) is a.get("equals",True)
            case "CUSTOM":
                predicate=str(a.get("predicate", ""));needed=int(a.get("count",1))
                if predicate in {"tag:#minecraft:logs","#minecraft:logs"}:return snapshot.matching_item_count("#minecraft:logs")>=needed
                if predicate in {"tag:#minecraft:planks","#minecraft:planks"}:return snapshot.matching_item_count("#minecraft:planks")>=needed
                if predicate in {"food_count","food"}:return snapshot.matching_item_count("food")>=needed
                if predicate=="new_observations":return action_result is not None and int(action_result.data.get("observations",0))>=needed
                if predicate=="action_succeeded":return action_result is not None and action_result.ok
                if predicate=="visible_block":
                    block_id=str(a.get("block_id", ""))
                    return any(str(row.get("id"))==block_id for row in snapshot.visible_blocks)
                if predicate=="fuel_count":
                    exact={"minecraft:coal","minecraft:charcoal","minecraft:coal_block","minecraft:dried_kelp_block","minecraft:lava_bucket","minecraft:bamboo","minecraft:stick","minecraft:blaze_rod"};suffixes=("_log","_wood","_planks","_stem","_hyphae")
                    count=sum(row.count for row in snapshot.inventory if row.id in exact or row.id.endswith(suffixes))
                    return count>=needed
                if predicate=="equipped_item":
                    slot=str(a.get("slot") or "MAINHAND").upper();item_id=str(a.get("item_id") or "")
                    if slot=="MAINHAND":return snapshot.main_hand_item==item_id
                    if slot=="OFFHAND":return snapshot.off_hand_item==item_id
                    return False
                if predicate=="build_complete":
                    if action_result is None:return False
                    checkpoint=action_result.data.get("checkpoint") or {}
                    return (
                        action_result.ok
                        and action_result.code=="BUILD_COMPLETE"
                        and action_result.data.get("complete") is True
                        and checkpoint.get("status")=="DONE"
                    )
                return False
            case _:return False

    def all(self,conditions:list[Condition],snapshot:StateSnapshot,action_result:ActionResult|None=None)->bool:
        return all(self.evaluate(c,snapshot,action_result) for c in conditions)

    def any(self,conditions:list[Condition],snapshot:StateSnapshot,action_result:ActionResult|None=None)->bool:
        return any(self.evaluate(c,snapshot,action_result) for c in conditions)
