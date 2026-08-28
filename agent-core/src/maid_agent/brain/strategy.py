from __future__ import annotations

import json,re
from typing import Any
from pydantic import BaseModel,Field,ValidationError

from maid_agent.actions.catalog import CATALOG,ToolValidationError
from maid_agent.capability.graph import CapabilityGraph
from maid_agent.goal.models import Condition,GoalType,PlanNodeKind,PlanStep,PlanUpdate
from maid_agent.memory.capability_gaps import CapabilityGapDraft
from maid_agent.prompts.loader import load_prompt
from maid_agent.protocol.models import StateSnapshot


class StrategyState(BaseModel):
    long_term_objective:str="在当前世界持续生存、自主发展，并提高长期安全性、生产与建设能力。"
    mid_term_objectives:list[str]=Field(default_factory=list)
    current_focus:str="建立基础生存闭环"
    known_constraints:list[str]=Field(default_factory=lambda:["严格生存","不能透视","不能传送或生成物品"])
    open_problems:list[str]=Field(default_factory=list)
    decision_summary:str="正在观察世界并建立第一个可验证目标。"
    evidence:list[str]=Field(default_factory=list)
    last_review_game_day:int=0
    threat_posture:str="LOW"


class StrategyDecision(BaseModel):
    keep_current_goal:bool=False
    goal_type:GoalType=GoalType.CUSTOM
    objective:str
    priority:int=Field(50,ge=0,le=100)
    success_conditions:list[Condition]
    steps:list[PlanStep]=Field(default_factory=list)
    plan_updates:list[PlanUpdate]=Field(default_factory=list)
    decision_summary:str
    evidence:list[str]=Field(default_factory=list)
    memory_queries:list[dict[str,Any]]=Field(default_factory=list)
    capability_target:str|None=None
    capability_gap:CapabilityGapDraft|None=None

    def validate_tools(self)->"StrategyDecision":
        if not self.keep_current_goal and not self.steps:raise ValueError("new goal requires at least one step")
        if self.capability_gap is not None and not self.keep_current_goal:
            raise ValueError("a capability gap must be reported while retaining the current goal for later adjustment")
        for root in self.steps:
            for step in root.iter_nodes():
                if step.kind in {PlanNodeKind.ACTION,PlanNodeKind.WAIT}:
                    CATALOG.validate(step.tool,step.args,allow_templates=True)
        return self


class DeterministicSurvivalPolicy:
    FOOD_ANIMALS={
        "minecraft:cow","minecraft:pig","minecraft:chicken","minecraft:sheep",
        "minecraft:rabbit","minecraft:cod","minecraft:salmon",
    }
    FOOD_DROPS={
        "minecraft:cow":"minecraft:beef","minecraft:pig":"minecraft:porkchop",
        "minecraft:chicken":"minecraft:chicken","minecraft:sheep":"minecraft:mutton",
        "minecraft:rabbit":"minecraft:rabbit","minecraft:cod":"minecraft:cod","minecraft:salmon":"minecraft:salmon",
    }

    def __init__(self,capabilities:CapabilityGraph|None=None):
        self.capabilities=capabilities or CapabilityGraph()

    def decide(self,snapshot:StateSnapshot,context:dict[str,Any]|None=None)->StrategyDecision:
        context=context or {};hostile=snapshot.nearest_hostile()
        if snapshot.health<=max(6.0,snapshot.max_health*.3):
            if hostile:
                steps=[PlanStep(description="远离最近真实威胁",tool="retreat_from",args={"uuid":hostile.uuid,"distance":18},success_conditions=[Condition(type="NO_HOSTILE_WITHIN",args={"radius":10})])]
                return StrategyDecision(goal_type=GoalType.RECOVER,objective="先脱离当前真实威胁",priority=100,
                    success_conditions=[Condition(type="NO_HOSTILE_WITHIN",args={"radius":10})],steps=steps,
                    decision_summary="生命值低且存在真实威胁，先撤退；随后再获取或使用食物。",evidence=[f"health={snapshot.health}/{snapshot.max_health}",f"hostile_uuid={hostile.uuid}"])
            elif snapshot.matching_item_count("food")>0:
                steps=[PlanStep(description="使用背包中的食物恢复",tool="eat",args={},success_conditions=[Condition(type="HEALTH_AT_LEAST",args={"value":max(8,snapshot.max_health*.45)})])]
            else:
                return self._food_decision(snapshot,context,priority=100)
            return StrategyDecision(goal_type=GoalType.RECOVER,objective="脱离当前危险并恢复到可继续行动的状态",priority=100,
                success_conditions=[Condition(type="HEALTH_AT_LEAST",args={"value":max(8,snapshot.max_health*.45)})],steps=steps,
                decision_summary="生命值低于安全阈值，优先恢复；撤退只使用快照中真实存在的敌人 UUID。",evidence=[f"health={snapshot.health}/{snapshot.max_health}",f"hostile_uuid={hostile.uuid if hostile else 'none'}"])

        food=snapshot.matching_item_count("food")
        if snapshot.hunger is not None and snapshot.hunger<=10 and food>0:
            return StrategyDecision(
                goal_type=GoalType.RECOVER,objective="进食并恢复安全饥饿值",priority=92,
                success_conditions=[Condition(type="HUNGER_AT_LEAST",args={"value":14})],
                steps=[PlanStep(description="吃背包中的真实食物",tool="eat",args={})],
                decision_summary="饥饿值已低于安全阈值，使用现有食物恢复后再继续发展。",
                evidence=[f"hunger={snapshot.hunger}",f"food_count={food}"],
            )

        if food==0 and snapshot.hunger is not None and snapshot.hunger<=8:
            return self._food_decision(snapshot,context,priority=95)

        missing=self.capabilities.first_missing(snapshot,"iron_pickaxe")
        if missing:
            labels={
                "wood":"获得第一批真实原木","planks":"合成基础木板","sticks":"合成工具所需木棍",
                "crafting_table_item":"合成工作台","crafting_station_ready":"部署并确认真实工作台",
                "wooden_pickaxe":"合成并装备木镐","stone":"用木镐获取足够圆石",
                "stone_pickaxe":"合成并装备石镐","stone_stockpile":"为熔炉收集圆石",
                "furnace_item":"合成熔炉","furnace_station_ready":"部署并确认真实熔炉",
                "fuel":"收集真实熔炉燃料","raw_iron":"用石镐获取可见铁矿的 raw iron",
                "iron_ingot":"在真实熔炉中冶炼铁锭","iron_stockpile":"冶炼制作铁镐所需铁锭",
                "iron_pickaxe":"合成并装备第一把铁镐",
            }
            steps=self.capabilities.next_steps(snapshot,missing)
            return StrategyDecision(
                goal_type=GoalType.IMPROVE_EQUIPMENT,
                objective=labels.get(missing,f"补齐生存前置能力 {missing}"),
                priority=85,
                success_conditions=[self.capabilities.condition(missing)],
                steps=steps,
                decision_summary="按真实后置条件继续木到石再到铁的最短缺失前置；工作站必须已放置在世界中。",
                evidence=[f"missing_capability={missing}",f"food_count={food}"],
                capability_target=missing,
            )

        if food<4:
            return self._food_decision(snapshot,context,priority=72)

        if not self.capabilities.has(snapshot,"storage_ready"):
            missing_storage=self.capabilities.first_missing(snapshot,"storage_ready") or "storage_ready"
            return StrategyDecision(
                goal_type=GoalType.STORE_ITEMS,objective="建立真实可用的基础储存",priority=62,
                success_conditions=[self.capabilities.condition(missing_storage)],
                steps=self.capabilities.next_steps(snapshot,missing_storage),
                decision_summary="铁制工具链已建立，继续用真实材料补齐基础储存。",
                evidence=[f"missing_capability={missing_storage}"],capability_target=missing_storage,
            )

        explore=self.capabilities.explore_steps(snapshot,"向尚未调查的安全方向探索")
        return StrategyDecision(goal_type=GoalType.EXPLORE,objective="在安全范围内调查新的资源与工作站",priority=50,
            success_conditions=[Condition(type="CUSTOM",args={"predicate":"new_observations","count":1})],steps=explore,
            decision_summary="基础工具与储存已可用，移动到新区域后再观察，避免在同一点无限 inspect。",evidence=[f"day={snapshot.day}",f"inventory_slots={len(snapshot.inventory)}"])

    def _food_decision(self,snapshot:StateSnapshot,context:dict[str,Any],*,priority:int)->StrategyDecision:
        animal=next((entity for entity in snapshot.nearby_entities if entity.type in self.FOOD_ANIMALS),None)
        food=snapshot.matching_item_count("food")
        if animal:
            steps=[
                PlanStep(description="猎取当前真实可见的食物动物",tool="attack_entity",args={"uuid":animal.uuid}),
                PlanStep(description="拾取女仆附近的真实食物掉落",tool="pickup_nearby",args={"radius":12,"item_id":self.FOOD_DROPS[animal.type]}),
            ]
            summary="发现可见的普通食物动物，使用真实战斗和掉落获取食物。"
            evidence=[f"food_source_uuid={animal.uuid}",f"food_source_type={animal.type}"]
            success=[Condition(type="TAG_COUNT",args={"tag":"food","count":food+1})]
        else:
            remembered=next((row for row in context.get("recalled_locations",[]) if {"food","farm"}.intersection(row.get("tags",[]))),None)
            if remembered:
                steps=[
                    PlanStep(description="返回记忆中的食物来源",tool="move_to",args={"x":remembered["x"],"y":remembered["y"],"z":remembered["z"],"range":3}),
                    PlanStep(description="重新观察该食物来源",tool="inspect_area",args={"radius":24},success_conditions=[Condition(type="CUSTOM",args={"predicate":"new_observations","count":1})]),
                ]
                summary="当前没有可见食物动物，先返回记忆中的食物地点并重新验证。";evidence=[f"memory_location={remembered.get('name','food')}" ];success=[Condition(type="CUSTOM",args={"predicate":"new_observations","count":1})]
            else:
                steps=self.capabilities.explore_steps(snapshot,"探索新的食物来源")
                summary="当前没有已知食物来源，移动到新区域探索，而不是在原地反复 inspect。";evidence=[f"food_count={food}"];success=[Condition(type="CUSTOM",args={"predicate":"new_observations","count":1})]
        return StrategyDecision(goal_type=GoalType.SECURE_FOOD,objective="寻找并取得真实食物来源",priority=priority,
            success_conditions=success,steps=steps,decision_summary=summary,evidence=evidence,capability_target="food")


def _json_object(text:str)->dict[str,Any]:
    text=text.strip()
    if text.startswith("```"):text=text.split("\n",1)[1].rsplit("```",1)[0]
    try:return json.loads(text)
    except json.JSONDecodeError:
        match=re.search(r"\{.*\}",text,re.S)
        if not match:raise
        return json.loads(match.group(0))


def parse_strategy_decision(text:str)->StrategyDecision:
    try:return StrategyDecision.model_validate(_json_object(text)).validate_tools()
    except (json.JSONDecodeError,ValidationError,ToolValidationError,ValueError) as exc:raise ValueError(f"invalid structured strategy response: {exc}") from exc


def strategy_prompt(*,context:dict[str,Any],state:StrategyState)->list[dict[str,str]]:
    payload={
        **context,
        "strategy_state":state.model_dump(mode="json"),
        "tool_catalog":CATALOG.prompt_payload(include_runtime=True),
        "temporary_plan_rules":{
            "default":"Use a one-off TEMPORARY plan for current-world tactics.",
            "node_kinds":["ACTION","WAIT","IF","BRANCH","REPEAT","WHILE","UNTIL","ABORT","PAUSE"],
            "control_conditions":"Only registered Condition types from the schema; CUSTOM is not permitted in control flow.",
            "loops":"Every loop requires max_iterations, max_duration_ticks, and exit_condition.",
            "updates":"When keep_current_goal is true, plan_updates may alter pending step args only.",
            "skills":"Do not create a Skill for a one-off strategy; only call an existing ACTIVE Skill when repeated reuse is already proven.",
        },
        "required_output_schema":StrategyDecision.model_json_schema(),
    }
    return [{"role":"system","content":load_prompt("runtime_system")},{"role":"user","content":json.dumps(payload,ensure_ascii=False,default=str)}]


def repair_prompt(bad_text:str,error:str)->list[dict[str,str]]:
    return [{"role":"system","content":"修复下面的 JSON，使其严格符合给定 StrategyDecision schema。只输出一个 JSON 对象，不改成解释。"},{"role":"user","content":json.dumps({"error":error,"bad_output":bad_text[:20000],"schema":StrategyDecision.model_json_schema(),"tools":CATALOG.prompt_payload()},ensure_ascii=False)}]
