from __future__ import annotations

from collections import Counter
from typing import Any
from maid_agent.memory.store import MemoryStore


class Scoreboard:
    def __init__(self,store:MemoryStore|None=None):
        self.store=store;raw=store.get_runtime_state("scoreboard",{}) if store else {}
        self.actions=Counter(raw.get("actions",{}));self.action_codes=Counter(raw.get("action_codes",{}));self.goals=Counter(raw.get("goals",{}));self.events=Counter(raw.get("events",{}))
    def action(self,status:str,code:str)->None:self.actions[status]+=1;self.action_codes[code]+=1;self._save()
    def goal(self,status:str)->None:self.goals[status]+=1;self._save()
    def event(self,event_type:str)->None:self.events[event_type]+=1;self._save()
    def snapshot(self)->dict[str,Any]:return {"actions":dict(self.actions),"action_codes":dict(self.action_codes),"goals":dict(self.goals),"events":dict(self.events)}
    def _save(self)->None:
        if self.store:self.store.set_runtime_state("scoreboard",self.snapshot())
