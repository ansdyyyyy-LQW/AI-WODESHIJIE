from __future__ import annotations

from maid_agent.brain.strategy import StrategyDecision
from maid_agent.capability.graph import CapabilityGraph
from maid_agent.actions.catalog import CATALOG
from maid_agent.goal.models import Goal,Plan,PlanNodeKind,PlanUpdate
from maid_agent.protocol.models import StateSnapshot


class Planner:
    def __init__(self,capabilities:CapabilityGraph|None=None):self.capabilities=capabilities or CapabilityGraph()
    def from_decision(self,decision:StrategyDecision,*,game_day:int,snapshot:StateSnapshot|None=None)->tuple[Goal,Plan]:
        steps=list(decision.steps)
        if snapshot is not None:
            target=self.capabilities.inferred_target(decision.objective)
            if target:
                prerequisite=self.capabilities.next_steps(snapshot,target)
                if prerequisite and not any(step.tool==prerequisite[0].tool and step.args==prerequisite[0].args for step in steps):steps=prerequisite+steps
        metadata={"capability_target":decision.capability_target} if decision.capability_target else {}
        goal=Goal(type=decision.goal_type,objective=decision.objective,priority=decision.priority,success_conditions=decision.success_conditions,created_game_day=game_day,source="runtime_llm",metadata=metadata)
        for root in steps:
            for step in root.iter_nodes():
                if step.kind in {PlanNodeKind.ACTION,PlanNodeKind.WAIT}:
                    step.args=CATALOG.validate(step.tool,step.args,allow_templates=True)
        plan=Plan(goal_id=goal.goal_id,steps=steps,created_game_day=game_day,plan_kind="TEMPORARY")
        return goal,plan

    def apply_updates(self,plan:Plan,updates:list[PlanUpdate])->list[str]:
        applied=[]
        for update in updates:
            target=next((node for node in plan.iter_nodes() if node.step_id==update.step_id),None)
            if target is None or target.kind not in {PlanNodeKind.ACTION,PlanNodeKind.WAIT}:continue
            candidate=dict(update.args) if update.replace else {**target.args,**update.args}
            normalized=CATALOG.validate(target.tool,candidate,allow_templates=True)
            validated=update.model_copy(update={"args":normalized,"replace":True})
            if plan.update_pending_step(validated):applied.append(str(update.step_id))
        return applied
