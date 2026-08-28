from __future__ import annotations

from maid_agent.goal.models import Goal,GoalStatus,Plan
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import ActionResult,StateSnapshot


class GoalManager:
    def __init__(self,store:MemoryStore,verifier:PostconditionVerifier):
        self.store=store;self.verifier=verifier;self.current:Goal|None=None;self._paused_stack:list[Goal]=[]

    def set(self,goal:Goal,*,pause_current:bool=True)->Goal:
        if pause_current and self.current and self.current.status==GoalStatus.ACTIVE:
            self.current.status=GoalStatus.PAUSED
            self._paused_stack.append(self.current)
            self._save(self.current)
        goal.status=GoalStatus.ACTIVE;self.current=goal;self._save(goal);return goal

    def create_child(self,goal:Goal,parent:Goal,*,parent_status:GoalStatus=GoalStatus.PAUSED)->Goal:
        parent.status=parent_status;goal.parent_goal_id=parent.goal_id
        if parent not in self._paused_stack:self._paused_stack.append(parent)
        self._save(parent);goal.status=GoalStatus.ACTIVE;self.current=goal;self._save(goal);return goal

    def replace(self, goal: Goal, *, reason: str, replacement_plan_id: str) -> Goal:
        """Replace a strategic goal without putting the obsolete goal on the resume stack."""
        previous = self.current
        if previous is not None:
            previous.status = GoalStatus.ABORTED
            previous.metadata["replacement_reason"] = reason
            previous.metadata["replacement_plan_id"] = replacement_plan_id
            self._save(previous)
        goal.status = GoalStatus.ACTIVE
        self.current = goal
        self._save(goal)
        return goal

    def verify(self,snapshot:StateSnapshot,action_result:ActionResult|None=None)->GoalStatus|None:
        goal=self.current
        if not goal:return None
        if goal.failure_conditions and self.verifier.any(goal.failure_conditions,snapshot,action_result):goal.status=GoalStatus.FAILED
        elif goal.success_conditions and self.verifier.all(goal.success_conditions,snapshot,action_result):goal.status=GoalStatus.SUCCESS
        elif goal.deadline_game_tick is not None and snapshot.game_tick>goal.deadline_game_tick:goal.status=GoalStatus.FAILED
        self._save(goal);return goal.status

    def resume_parent_if_ready(self)->Goal|None:
        if not self.current or self.current.status!=GoalStatus.SUCCESS:return None
        parent_id=self.current.parent_goal_id
        if parent_id is None:return None
        for index in range(len(self._paused_stack)-1,-1,-1):
            parent=self._paused_stack[index]
            if parent.goal_id==parent_id:
                self._paused_stack.pop(index);parent.status=GoalStatus.ACTIVE;self.current=parent;self._save(parent);return parent
        return None

    def mark_blocked(self,code:str,*,game_tick:int=0)->None:
        if not self.current:return
        self.current.status=GoalStatus.BLOCKED;self.current.metadata["last_error_code"]=code
        self.store.record_event(game_day=self.current.created_game_day,game_tick=game_tick,event_type="GOAL_BLOCKED",severity="WARN",payload={"goal_id":str(self.current.goal_id),"code":code})
        self._save(self.current)

    def restore(self,goal:Goal|None)->None:
        self.current=goal;self._paused_stack=[]
        seen:set[str]=set()
        parent_id=str(goal.parent_goal_id) if goal and goal.parent_goal_id else None
        chain:list[Goal]=[]
        while parent_id and parent_id not in seen:
            seen.add(parent_id);raw=self.store.load_goal(parent_id)
            if not raw:break
            parent=Goal.model_validate(raw);chain.append(parent)
            parent_id=str(parent.parent_goal_id) if parent.parent_goal_id else None
        self._paused_stack=list(reversed(chain))

    def _save(self,goal:Goal)->None:self.store.save_model("goals","goal_id",str(goal.goal_id),goal,status=goal.status)
