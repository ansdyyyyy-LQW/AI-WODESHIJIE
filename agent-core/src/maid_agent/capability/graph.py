from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any, TYPE_CHECKING

from maid_agent.goal.models import Condition, PlanStep
from maid_agent.protocol.models import StateSnapshot

if TYPE_CHECKING:
    from maid_agent.memory.store import MemoryStore


@dataclass(frozen=True)
class CapabilityNode:
    capability: str
    requirements: tuple[str, ...] = ()
    evidence_items: tuple[str, ...] = ()
    minimum_count: int = 1


class CapabilityGraph:
    """Postcondition-driven prerequisites for the real early-survival chain.

    Inventory items and deployed workstations intentionally use different nodes.
    A table or furnace in inventory never satisfies a ``*_station_ready`` node.
    """

    WORKSTATION_BLOCKS = {
        "crafting_station_ready": "minecraft:crafting_table",
        "furnace_station_ready": "minecraft:furnace",
        "storage_ready": "minecraft:chest",
    }
    WORKSTATION_KINDS = {
        "crafting_station_ready": "crafting_table",
        "furnace_station_ready": "furnace",
        "storage_ready": "container",
    }
    PICKAXE_TIERS = {
        "wooden_pickaxe": (
            "minecraft:wooden_pickaxe", "minecraft:stone_pickaxe",
            "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe",
            "minecraft:netherite_pickaxe",
        ),
        "stone_pickaxe": (
            "minecraft:stone_pickaxe", "minecraft:iron_pickaxe",
            "minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe",
        ),
        "iron_pickaxe": (
            "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe",
            "minecraft:netherite_pickaxe",
        ),
    }

    def __init__(self, store: MemoryStore | None = None):
        self.store = store
        self.nodes = {
            "wood": CapabilityNode("wood", evidence_items=("#minecraft:logs",)),
            "planks": CapabilityNode("planks", ("wood",), ("#minecraft:planks",), 4),
            "sticks": CapabilityNode("sticks", ("planks",), ("minecraft:stick",), 2),
            "crafting_table_item": CapabilityNode(
                "crafting_table_item", ("planks",), ("minecraft:crafting_table",)
            ),
            "crafting_station_ready": CapabilityNode(
                "crafting_station_ready", ("crafting_table_item",)
            ),
            "wooden_pickaxe": CapabilityNode(
                "wooden_pickaxe",
                ("crafting_station_ready", "sticks", "planks"),
                ("minecraft:wooden_pickaxe",),
            ),
            "stone": CapabilityNode(
                "stone", ("wooden_pickaxe",), ("minecraft:cobblestone",), 3
            ),
            "stone_pickaxe": CapabilityNode(
                "stone_pickaxe",
                ("crafting_station_ready", "sticks", "stone"),
                ("minecraft:stone_pickaxe",),
            ),
            "stone_stockpile": CapabilityNode(
                "stone_stockpile", ("stone_pickaxe",), ("minecraft:cobblestone",), 8
            ),
            "furnace_item": CapabilityNode(
                "furnace_item",
                ("crafting_station_ready", "stone_stockpile"),
                ("minecraft:furnace",),
            ),
            "furnace_station_ready": CapabilityNode(
                "furnace_station_ready", ("furnace_item",)
            ),
            "fuel": CapabilityNode("fuel", ("wood",)),
            "raw_iron": CapabilityNode(
                "raw_iron", ("stone_pickaxe",), ("minecraft:raw_iron",)
            ),
            "iron_ingot": CapabilityNode(
                "iron_ingot",
                ("raw_iron", "furnace_station_ready", "fuel"),
                ("minecraft:iron_ingot",),
            ),
            "iron_stockpile": CapabilityNode(
                "iron_stockpile",
                ("raw_iron", "furnace_station_ready", "fuel"),
                ("minecraft:iron_ingot",),
                3,
            ),
            "iron_pickaxe": CapabilityNode(
                "iron_pickaxe",
                ("iron_stockpile", "crafting_station_ready", "sticks"),
                ("minecraft:iron_pickaxe",),
            ),
            "storage_item": CapabilityNode(
                "storage_item", ("crafting_station_ready", "planks"), ("minecraft:chest",)
            ),
            "storage_ready": CapabilityNode("storage_ready", ("storage_item",)),
            "food": CapabilityNode("food", evidence_items=("food",), minimum_count=4),
        }

    @staticmethod
    def _count(snapshot: StateSnapshot, item: str) -> int:
        if item.startswith("#") or item == "food":
            return snapshot.matching_item_count(item)
        return snapshot.item_count(item)

    @staticmethod
    def _visible_block(snapshot: StateSnapshot, block_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in snapshot.visible_blocks if str(row.get("id")) == block_id),
            None,
        )

    @staticmethod
    def _fuel_count(snapshot: StateSnapshot) -> int:
        exact = {"minecraft:coal","minecraft:charcoal","minecraft:coal_block","minecraft:dried_kelp_block","minecraft:lava_bucket","minecraft:bamboo","minecraft:stick","minecraft:blaze_rod"}
        suffixes = ("_log", "_wood", "_planks", "_stem", "_hyphae")
        return sum(
            row.count for row in snapshot.inventory if row.id in exact or row.id.endswith(suffixes)
        )

    def has(self, snapshot: StateSnapshot, capability: str) -> bool:
        if capability in self.WORKSTATION_BLOCKS:
            row = self._visible_block(snapshot, self.WORKSTATION_BLOCKS[capability])
            if row is None:
                return False
            # CraftAction is deliberately non-teleporting and only accepts a table in
            # its real 4-block interaction neighbourhood.  A distant visible table is
            # remembered, then approached through _station_steps before it is ready.
            if capability == "crafting_station_ready":
                try:
                    return (
                        abs(float(row["x"]) - snapshot.position.x) <= 4
                        and abs(float(row["y"]) - snapshot.position.y) <= 2
                        and abs(float(row["z"]) - snapshot.position.z) <= 4
                    )
                except (KeyError, TypeError, ValueError):
                    return False
            return True
        if capability == "fuel":
            return self._fuel_count(snapshot) > 0
        if capability in self.PICKAXE_TIERS:
            usable = set(self.PICKAXE_TIERS[capability])
            return snapshot.main_hand_item in usable or any(row.id in usable for row in snapshot.inventory)
        node = self.nodes.get(capability)
        if node is None or not node.evidence_items:
            return False
        return any(
            self._count(snapshot, item) >= node.minimum_count for item in node.evidence_items
        )

    def first_missing(self, snapshot: StateSnapshot, capability: str) -> str | None:
        node = self.nodes.get(capability)
        if node is None:
            return capability
        # Existing real evidence satisfies a capability even when the Maid no longer
        # carries every ingredient or workstation that originally produced it.
        if self.has(snapshot,capability):
            return None
        for requirement in node.requirements:
            missing = self.first_missing(snapshot, requirement)
            if missing:
                return missing
        return capability

    @staticmethod
    def _plank_item(snapshot: StateSnapshot) -> str:
        for row in snapshot.inventory:
            item = row.id
            namespace,separator,name=item.partition(":")
            normalized=name.removeprefix("stripped_")
            prefix=f"{namespace}{separator}" if separator else ""
            if normalized.endswith("_log"):
                return prefix+normalized[:-4] + "_planks"
            if normalized.endswith("_wood"):
                return prefix+normalized[:-5] + "_planks"
            if item.endswith("_stem") or item.endswith("_hyphae"):
                return "minecraft:crimson_planks" if "crimson" in item else "minecraft:warped_planks"
        return "minecraft:oak_planks"

    def _known_workstation(
        self, snapshot: StateSnapshot, capability: str
    ) -> dict[str, Any] | None:
        if self.store is None:
            return None
        rows = self.store.recall_structures(
            kind=self.WORKSTATION_KINDS[capability], dimension=snapshot.dimension, limit=20
        )
        if not rows:
            return None
        return min(
            rows,
            key=lambda row: (row["x"] - snapshot.position.x) ** 2
            + (row["y"] - snapshot.position.y) ** 2
            + (row["z"] - snapshot.position.z) ** 2,
        )

    def _station_steps(self, snapshot: StateSnapshot, capability: str) -> list[PlanStep]:
        block_id = self.WORKSTATION_BLOCKS[capability]
        known = self._known_workstation(snapshot, capability)
        if known is not None:
            return [
                PlanStep(
                    description=f"返回已记录的 {block_id} 工作站",
                    tool="move_to",
                    args={"x": known["x"], "y": known["y"], "z": known["z"], "range": 2.5},
                ),
                PlanStep(
                    description=f"重新观察并确认 {block_id} 仍存在",
                    tool="find_visible_block",
                    args={"query": block_id, "limit": 4},
                    success_conditions=[
                        Condition(
                            type="CUSTOM",
                            args={"predicate": "visible_block", "block_id": block_id},
                        )
                    ],
                ),
            ]
        return [
            PlanStep(
                description=f"寻找附近可合法部署 {block_id} 的位置",
                tool="find_place_position",
                args={"item_id": block_id, "radius": 4},
            ),
            PlanStep(
                description=f"从女仆背包真实放置 {block_id}",
                tool="place_block",
                args={
                    "x": "$previous.position.x",
                    "y": "$previous.position.y",
                    "z": "$previous.position.z",
                    "item_id": block_id,
                    "face": "UP",
                },
                success_conditions=[
                    Condition(type="WORLD_DELTA", args={"key": "block_placed"})
                ],
            ),
            PlanStep(
                description=f"从新世界快照确认 {block_id} 已部署",
                tool="find_visible_block",
                args={"query": block_id, "limit": 4},
                success_conditions=[
                    Condition(
                        type="CUSTOM",
                        args={"predicate": "visible_block", "block_id": block_id},
                    )
                ],
            ),
        ]

    def forget_stale_workstation(self,snapshot:StateSnapshot,capability:str)->int:
        if self.store is None or capability not in self.WORKSTATION_KINDS:return 0
        return self.store.mark_structure_missing(kind=self.WORKSTATION_KINDS[capability],dimension=snapshot.dimension,x=snapshot.position.x,y=snapshot.position.y,z=snapshot.position.z,radius=5)

    @staticmethod
    def _item_condition(item_id: str, count: int) -> Condition:
        return Condition(type="ITEM_COUNT", args={"item_id": item_id, "count": count})

    def next_steps(self, snapshot: StateSnapshot, capability: str) -> list[PlanStep]:
        missing = self.first_missing(snapshot, capability)
        if missing is None:
            return []
        if missing in self.WORKSTATION_BLOCKS:
            return self._station_steps(snapshot, missing)

        plank = self._plank_item(snapshot)
        wood_before=snapshot.matching_item_count("#minecraft:logs")
        wood_steps = [
                PlanStep(
                    description="寻找真实可见原木",
                    tool="find_visible_block",
                    args={"query": "#minecraft:logs", "limit": 16},
                ),
                PlanStep(
                    description="移动到目标并由女仆真实挖掘原木",
                    tool="break_block",
                    args={
                        "x": "$previous.position.x",
                        "y": "$previous.position.y",
                        "z": "$previous.position.z",
                    },
                    success_conditions=[
                        Condition(type="WORLD_DELTA", args={"key": "block_broken"})
                    ],
                ),
                PlanStep(
                    description="拾取刚刚真实掉落的原木",
                    tool="pickup_nearby",
                    args={"radius":8},
                    success_conditions=[Condition(type="TAG_COUNT",args={"tag":"#minecraft:logs","count":wood_before+1})],
                ),
            ]
        templates: dict[str, list[PlanStep]] = {
            "wood": wood_steps,
            "planks": [
                PlanStep(
                    description="把已有原木合成为木板",
                    tool="craft",
                    args={"item_id": plank, "count": 4},
                    success_conditions=[
                        Condition(type="TAG_COUNT", args={"tag": "#minecraft:planks", "count": 4})
                    ],
                )
            ],
            "sticks": [
                PlanStep(
                    description="用真实木板合成木棍",
                    tool="craft",
                    args={"item_id": "minecraft:stick", "count": 4},
                    success_conditions=[self._item_condition("minecraft:stick", 2)],
                )
            ],
            "crafting_table_item": [
                PlanStep(
                    description="合成工作台物品",
                    tool="craft",
                    args={"item_id": "minecraft:crafting_table", "count": 1},
                    success_conditions=[self._item_condition("minecraft:crafting_table", 1)],
                )
            ],
            "wooden_pickaxe": [
                PlanStep(
                    description="在真实工作台旁合成木镐",
                    tool="craft",
                    args={"item_id": "minecraft:wooden_pickaxe", "count": 1},
                    success_conditions=[self._item_condition("minecraft:wooden_pickaxe", 1)],
                ),
                PlanStep(
                    description="装备木镐到主手",
                    tool="equip",
                    args={"item_id": "minecraft:wooden_pickaxe", "slot": "MAINHAND"},
                    success_conditions=[Condition(type="CUSTOM",args={"predicate":"equipped_item","slot":"MAINHAND","item_id":"minecraft:wooden_pickaxe"})],
                ),
            ],
            "stone": self._mine_steps(
                snapshot, "minecraft:wooden_pickaxe", "minecraft:stone", "minecraft:cobblestone"
            ),
            "stone_pickaxe": [
                PlanStep(
                    description="在真实工作台旁合成石镐",
                    tool="craft",
                    args={"item_id": "minecraft:stone_pickaxe", "count": 1},
                    success_conditions=[self._item_condition("minecraft:stone_pickaxe", 1)],
                ),
                PlanStep(
                    description="装备石镐到主手",
                    tool="equip",
                    args={"item_id": "minecraft:stone_pickaxe", "slot": "MAINHAND"},
                    success_conditions=[Condition(type="CUSTOM",args={"predicate":"equipped_item","slot":"MAINHAND","item_id":"minecraft:stone_pickaxe"})],
                ),
            ],
            "stone_stockpile": self._mine_steps(
                snapshot, "minecraft:stone_pickaxe", "minecraft:stone", "minecraft:cobblestone"
            ),
            "furnace_item": [
                PlanStep(
                    description="在真实工作台旁合成熔炉",
                    tool="craft",
                    args={"item_id": "minecraft:furnace", "count": 1},
                    success_conditions=[self._item_condition("minecraft:furnace", 1)],
                )
            ],
            "fuel": wood_steps,
            "raw_iron": self._mine_steps(
                snapshot,
                "minecraft:stone_pickaxe",
                "#minecraft:iron_ores",
                "minecraft:raw_iron",
            ),
            "iron_ingot": self._smelt_iron_steps(snapshot),
            "iron_stockpile": self._smelt_iron_steps(snapshot),
            "iron_pickaxe": [
                PlanStep(
                    description="在真实工作台旁合成铁镐",
                    tool="craft",
                    args={"item_id": "minecraft:iron_pickaxe", "count": 1},
                    success_conditions=[self._item_condition("minecraft:iron_pickaxe", 1)],
                ),
                PlanStep(
                    description="装备铁镐到主手",
                    tool="equip",
                    args={"item_id": "minecraft:iron_pickaxe", "slot": "MAINHAND"},
                    success_conditions=[Condition(type="CUSTOM",args={"predicate":"equipped_item","slot":"MAINHAND","item_id":"minecraft:iron_pickaxe"})],
                ),
            ],
            "storage_item": [
                PlanStep(
                    description="用真实木板合成箱子",
                    tool="craft",
                    args={"item_id": "minecraft:chest", "count": 1},
                    success_conditions=[self._item_condition("minecraft:chest", 1)],
                )
            ],
            "food": self.explore_steps(snapshot, "探索新的食物来源"),
        }
        return templates.get(missing, self.explore_steps(snapshot, f"确认 {missing} 的真实来源"))

    def _mine_steps(
        self, snapshot: StateSnapshot, tool_item: str, query: str, output_item: str
    ) -> list[PlanStep]:
        before = snapshot.item_count(output_item)
        steps: list[PlanStep] = []
        if snapshot.main_hand_item != tool_item:
            steps.append(
                PlanStep(
                    description=f"装备挖掘 {query} 所需工具",
                    tool="equip",
                    args={"item_id": tool_item, "slot": "MAINHAND"},
                )
            )
        if query == "#minecraft:iron_ores":
            target_visible = any("iron_ore" in str(row.get("id", "")) for row in snapshot.visible_blocks)
        else:
            target_visible = any(str(row.get("id", "")) == query for row in snapshot.visible_blocks)
        if not target_visible:
            if query == "#minecraft:iron_ores":
                # Ordinary excavation, never an underground ore-coordinate scan.
                # Peel one currently visible stone-family block, pick up its real
                # drop, then observe the newly exposed face on the next snapshot.
                exposed = next(
                    (
                        str(row.get("id"))
                        for row in snapshot.visible_blocks
                        if str(row.get("id"))
                        in {
                            "minecraft:stone", "minecraft:deepslate", "minecraft:tuff",
                            "minecraft:granite", "minecraft:diorite", "minecraft:andesite",
                        }
                    ),
                    None,
                )
                if exposed:
                    drop = "minecraft:cobblestone" if exposed == "minecraft:stone" else (
                        "minecraft:cobbled_deepslate" if exposed == "minecraft:deepslate" else exposed
                    )
                    steps.extend(
                        [
                            PlanStep(
                                description=f"普通挖掘已暴露的 {exposed}，扩展铁矿搜索面",
                                tool="find_visible_block",
                                args={"query": exposed, "limit": 16},
                            ),
                            PlanStep(
                                description="由女仆近距离挖掉该暴露方块",
                                tool="break_block",
                                args={
                                    "x": "$previous.position.x",
                                    "y": "$previous.position.y",
                                    "z": "$previous.position.z",
                                },
                                success_conditions=[Condition(type="WORLD_DELTA", args={"key": "block_broken"})],
                            ),
                            PlanStep(
                                description="拾取普通挖掘产生的真实掉落物",
                                tool="pickup_nearby",
                                args={"radius": 8, "item_id": drop},
                            ),
                            PlanStep(
                                description="观察挖掘后新暴露的方块",
                                tool="inspect_area",
                                args={"radius": 32},
                                success_conditions=[Condition(type="CUSTOM", args={"predicate": "new_observations", "count": 1})],
                            ),
                        ]
                    )
                    return steps
            steps.extend(self.explore_steps(snapshot, f"探索新的暴露面以寻找 {query}"))
            return steps
        steps.extend([
            PlanStep(
                description=f"只搜索真实可见的 {query}",
                tool="find_visible_block",
                args={"query": query, "limit": 16},
            ),
            PlanStep(
                description=f"由女仆近距离真实挖掘 {query}",
                tool="break_block",
                args={
                    "x": "$previous.position.x",
                    "y": "$previous.position.y",
                    "z": "$previous.position.z",
                },
                success_conditions=[Condition(type="WORLD_DELTA",args={"key":"block_broken"})],
            ),
            PlanStep(
                description=f"拾取真实掉落的 {output_item}",
                tool="pickup_nearby",
                args={"radius":8,"item_id":output_item},
                success_conditions=[self._item_condition(output_item,before+1)],
            ),
        ])
        return steps

    def _smelt_iron_steps(self, snapshot: StateSnapshot, count: int = 1) -> list[PlanStep]:
        before = snapshot.item_count("minecraft:iron_ingot")
        batch = max(1, min(256, int(count), snapshot.item_count("minecraft:raw_iron")))
        return [
            PlanStep(
                description="找到已部署并可见的真实熔炉",
                tool="find_visible_block",
                args={"query": "minecraft:furnace", "limit": 4},
            ),
            PlanStep(
                description="在真实熔炉中消耗 raw iron 与燃料",
                tool="smelt",
                args={
                    "x": "$previous.position.x",
                    "y": "$previous.position.y",
                    "z": "$previous.position.z",
                    "input_item_id": "minecraft:raw_iron",
                    "output_item_id": "minecraft:iron_ingot",
                    "count": batch,
                },
                success_conditions=[self._item_condition("minecraft:iron_ingot", before + batch)],
                timeout_ticks=2400,
            ),
        ]

    def condition(self, capability: str) -> Condition:
        if capability in self.WORKSTATION_BLOCKS:
            return Condition(
                type="CUSTOM",
                args={
                    "predicate": "visible_block",
                    "block_id": self.WORKSTATION_BLOCKS[capability],
                },
            )
        if capability == "fuel":
            return Condition(type="CUSTOM", args={"predicate": "fuel_count", "count": 1})
        if capability in self.PICKAXE_TIERS:
            return Condition(type="CUSTOM",args={"predicate":"equipped_item","slot":"MAINHAND","item_id":self.nodes[capability].evidence_items[0]})
        node = self.nodes[capability]
        item = node.evidence_items[0]
        if item.startswith("#") or item == "food":
            return Condition(type="TAG_COUNT", args={"tag": item, "count": node.minimum_count})
        return self._item_condition(item, node.minimum_count)

    def resolve_material(self, item_id: str) -> dict[str, Any]:
        exact = {
            "minecraft:crafting_table": ("craft", "crafting_table_item"),
            "minecraft:furnace": ("craft", "furnace_item"),
            "minecraft:chest": ("craft", "storage_item"),
            "minecraft:cobblestone": ("mine", "stone_stockpile"),
            "minecraft:raw_iron": ("mine", "raw_iron"),
            "minecraft:iron_ingot": ("smelt", "iron_ingot"),
            "minecraft:iron_block": ("craft", "iron_stockpile"),
        }
        if item_id in exact:
            source, capability = exact[item_id]
            return {"source": source, "capability": capability, "item_id": item_id}
        if item_id.endswith("_planks"):
            return {"source": "craft", "capability": "planks", "item_id": item_id}
        if item_id.endswith("_log") or item_id.endswith("_stem"):
            return {"source": "mine", "capability": "wood", "item_id": item_id}
        known = []
        if self.store is not None:
            known = self.store.recall_resources(resource_query=item_id.split(":")[-1], limit=4)
        return {
            "source": "known_resource" if known else "unknown",
            "capability": None,
            "item_id": item_id,
            "known_resources": known,
        }

    def acquisition_steps(
        self, snapshot: StateSnapshot, item_id: str, count: int
    ) -> list[PlanStep]:
        count = max(1, int(count))
        resolved = self.resolve_material(item_id)
        if item_id.endswith("_planks"):
            wood_prefix=item_id.removeprefix("minecraft:").removesuffix("_planks")
            raw_candidates={f"minecraft:{wood_prefix}_log",f"minecraft:{wood_prefix}_wood",f"minecraft:stripped_{wood_prefix}_log",f"minecraft:stripped_{wood_prefix}_wood"}
            if wood_prefix in {"crimson","warped"}:raw_candidates={f"minecraft:{wood_prefix}_stem",f"minecraft:{wood_prefix}_hyphae",f"minecraft:stripped_{wood_prefix}_stem",f"minecraft:stripped_{wood_prefix}_hyphae"}
            if not any(snapshot.item_count(raw)>0 for raw in raw_candidates):
                visible=next((str(row.get("id")) for row in snapshot.visible_blocks if str(row.get("id")) in raw_candidates),None)
                query=visible or sorted(raw_candidates)[0]
                if visible:
                    return [PlanStep(description=f"寻找真实可见的 {wood_prefix} 木材",tool="find_visible_block",args={"query":query,"limit":16}),PlanStep(description=f"由女仆真实获取 {wood_prefix} 木材",tool="break_block",args={"x":"$previous.position.x","y":"$previous.position.y","z":"$previous.position.z"},success_conditions=[Condition(type="WORLD_DELTA",args={"key":"block_broken"})]),PlanStep(description=f"拾取真实掉落的 {query}",tool="pickup_nearby",args={"radius":8,"item_id":query})]
                return self.explore_steps(snapshot,f"探索并寻找 {wood_prefix} 木材")
            return [
                PlanStep(
                    description=f"把已有原木合成为 {item_id}",
                    tool="craft",
                    args={"item_id": item_id, "count": min(256, count)},
                    success_conditions=[
                        self._item_condition(item_id, snapshot.item_count(item_id) + min(256, count))
                    ],
                )
            ]

        if item_id.endswith(("_log", "_wood", "_stem", "_hyphae")):
            visible = any(str(row.get("id")) == item_id for row in snapshot.visible_blocks)
            if not visible:
                return self.explore_steps(snapshot, f"探索并寻找 {item_id} 的真实来源")
            before = snapshot.item_count(item_id)
            return [
                PlanStep(
                    description=f"寻找真实可见的 {item_id}",
                    tool="find_visible_block",
                    args={"query": item_id, "limit": 16},
                ),
                PlanStep(
                    description=f"由女仆近距离挖掉 {item_id}",
                    tool="break_block",
                    args={
                        "x": "$previous.position.x", "y": "$previous.position.y",
                        "z": "$previous.position.z",
                    },
                    success_conditions=[Condition(type="WORLD_DELTA", args={"key": "block_broken"})],
                ),
                PlanStep(
                    description=f"拾取真实掉落的 {item_id}",
                    tool="pickup_nearby",
                    args={"radius": 8, "item_id": item_id},
                    success_conditions=[self._item_condition(item_id, before + 1)],
                ),
            ]

        if item_id == "minecraft:cobblestone":
            tool = self._best_pickaxe(snapshot, "wooden_pickaxe")
            if tool is None:
                return self.next_steps(snapshot, "wooden_pickaxe")
            return self._mine_steps(snapshot, tool, "minecraft:stone", item_id)

        if item_id == "minecraft:raw_iron":
            tool = self._best_pickaxe(snapshot, "stone_pickaxe")
            if tool is None:
                return self.next_steps(snapshot, "stone_pickaxe")
            return self._mine_steps(snapshot, tool, "#minecraft:iron_ores", item_id)

        if item_id == "minecraft:iron_ingot":
            for prerequisite in ("raw_iron", "furnace_station_ready", "fuel"):
                if not self.has(snapshot, prerequisite):
                    return self.next_steps(snapshot, prerequisite)
            return self._smelt_iron_steps(snapshot, count)

        if item_id == "minecraft:iron_block":
            available = snapshot.item_count("minecraft:iron_ingot")
            craftable = min(count, available // 9, 256)
            if craftable <= 0:
                return self.acquisition_steps(snapshot, "minecraft:iron_ingot", max(1, 9 - available))
            return [
                PlanStep(
                    description="用真实铁锭合成建筑所需铁块",
                    tool="craft",
                    args={"item_id": item_id, "count": craftable},
                    success_conditions=[
                        self._item_condition(item_id, snapshot.item_count(item_id) + craftable)
                    ],
                )
            ]

        recipe_materials = {
            "minecraft:crafting_table": ("#minecraft:planks", 4, False),
            "minecraft:furnace": ("minecraft:cobblestone", 8, True),
            "minecraft:chest": ("#minecraft:planks", 8, True),
        }
        if item_id in recipe_materials:
            material, per_item, needs_table = recipe_materials[item_id]
            if needs_table and not self.has(snapshot, "crafting_station_ready"):
                return self.next_steps(snapshot, "crafting_station_ready")
            batch = min(256, count)
            available = snapshot.matching_item_count("#minecraft:planks") if material == "#minecraft:planks" else snapshot.item_count(material)
            required = per_item * batch
            if available < required:
                source_item = self._plank_item(snapshot) if material == "#minecraft:planks" else material
                return self.acquisition_steps(snapshot, source_item, required - available)
            return [
                PlanStep(
                    description=f"用真实材料合成建筑所需 {item_id}",
                    tool="craft",
                    args={"item_id": item_id, "count": batch},
                    success_conditions=[self._item_condition(item_id, snapshot.item_count(item_id) + batch)],
                )
            ]

        capability = resolved.get("capability")
        if capability:
            return self.next_steps(snapshot, str(capability))
        known = list(resolved.get("known_resources") or [])
        if known:
            target = known[0]
            return [
                PlanStep(
                    description=f"返回记忆中的 {item_id} 资源位置",
                    tool="move_to",
                    args={"x": target["x"], "y": target["y"], "z": target["z"], "range": 2.5},
                ),
                PlanStep(
                    description=f"重新观察并确认 {item_id}",
                    tool="find_visible_block",
                    args={"query": str(target.get("block_id") or item_id), "limit": 8},
                ),
                PlanStep(
                    description=f"真实挖掘 {item_id}",
                    tool="break_block",
                    args={
                        "x": "$previous.position.x",
                        "y": "$previous.position.y",
                        "z": "$previous.position.z",
                    },
                ),
                PlanStep(
                    description=f"拾取挖掘 {item_id} 产生的真实掉落物",
                    tool="pickup_nearby",
                    args={"radius": 8},
                ),
            ]
        return self.explore_steps(snapshot, f"探索 {item_id} 的真实来源")

    def _best_pickaxe(self, snapshot: StateSnapshot, minimum: str) -> str | None:
        usable = self.PICKAXE_TIERS[minimum]
        carried = {row.id for row in snapshot.inventory}
        return next(
            (item for item in reversed(usable) if item == snapshot.main_hand_item or item in carried),
            None,
        )

    @staticmethod
    def explore_steps(snapshot: StateSnapshot, description: str) -> list[PlanStep]:
        directions = ((12, 0), (0, 12), (-12, 0), (0, -12))
        dx, dz = directions[(snapshot.day + snapshot.game_tick // 200) % len(directions)]
        return [
            PlanStep(
                description=description,
                tool="move_to",
                args={
                    "x": floor(snapshot.position.x) + dx,
                    "y": floor(snapshot.position.y),
                    "z": floor(snapshot.position.z) + dz,
                    "range": 2.5,
                },
            ),
            PlanStep(
                description="在新位置观察可见资源与实体",
                tool="inspect_area",
                args={"radius": 32},
                success_conditions=[
                    Condition(type="CUSTOM", args={"predicate": "new_observations", "count": 1})
                ],
            ),
        ]

    def inferred_target(self, text: str) -> str | None:
        normalized = text.lower()
        exact = self.resolve_material(normalized)
        if exact.get("capability"):
            return str(exact["capability"])
        mapping = (
            ("iron_pickaxe", "iron_pickaxe"),
            ("铁镐", "iron_pickaxe"),
            ("iron", "iron_ingot"),
            ("铁", "iron_ingot"),
            ("furnace", "furnace_station_ready"),
            ("熔炉", "furnace_station_ready"),
            ("chest", "storage_ready"),
            ("箱", "storage_ready"),
            ("pickaxe", "stone_pickaxe"),
            ("镐", "stone_pickaxe"),
            ("food", "food"),
            ("食物", "food"),
            ("plank", "planks"),
            ("木板", "planks"),
            ("wood", "wood"),
            ("原木", "wood"),
        )
        return next((cap for token, cap in mapping if token in normalized), None)
