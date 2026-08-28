from __future__ import annotations

from typing import Any

from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import StateSnapshot
from maid_agent.skills.store import SkillStore
from maid_agent.threat.analytics import ThreatAnalytics


class ContextBuilder:
    def __init__(self,store:MemoryStore,skills:SkillStore,threats:ThreatAnalytics):
        self.store=store;self.skills=skills;self.threats=threats

    def build(self,*,snapshot:StateSnapshot,goal:Any=None,plan:Any=None,strategy:Any=None,reflections:list[dict[str,Any]]|None=None)->dict[str,Any]:
        recall=self.store.recall_for_goal(goal,snapshot) if goal else {
            "locations":self.store.recall_locations(dimension=snapshot.dimension,near=snapshot.position.model_dump(),limit=8),
            "resources":self.store.recall_resources(dimension=snapshot.dimension,near=snapshot.position.model_dump(),limit=8),
            "failures":self.store.recent_events(limit=8,min_day=max(0,snapshot.day-3),types=["ACTION_FAILED","ACTION_STUCK","GOAL_BLOCKED","MAID_DEATH"]),
            "structures":self.store.recall_structures(dimension=snapshot.dimension,limit=8),
        }
        return {
            "current_snapshot":snapshot.model_dump(mode="json"),
            "active_goal":goal.model_dump(mode="json") if goal else None,
            "active_plan":plan.model_dump(mode="json") if plan else None,
            "strategy_state":strategy.model_dump(mode="json") if strategy else None,
            "recent_high_signal_events":self.store.recent_events(limit=30,min_day=max(0,snapshot.day-5)),
            "recalled_locations":recall["locations"][:12],
            "recalled_resources":recall["resources"][:16],
            "recalled_structures":recall.get("structures",[])[:12],
            "recalled_failures":recall["failures"][:12],
            "available_skills":{
                "role":"仅复用已经批准且有稳定重复价值的能力；一次性策略留在临时计划中。",
                "items":self.skills.rank_for_goal(goal,context={"snapshot":snapshot.model_dump(mode="json")},limit=12) if goal else self.skills.list_active_skills(limit=12),
            },
            "threat_summary":self.threats.context_summary(current_day=snapshot.day),
            "recent_reflections":(reflections or [])[-12:],
            "capability_gaps":{
                "role":"仅用于说明当前动作边界；普通问题仍按成熟能力→临时计划→调整计划处理。",
                "items":self.store.list_capability_gaps(limit=20),
            },
        }
