from __future__ import annotations

from dataclasses import dataclass,field
from typing import Any


@dataclass
class ReflectionEntry:
    source:str;code:str;summary:str;context:dict[str,Any]=field(default_factory=dict)
    count:int=1


class ReflectionQueue:
    def __init__(self,max_entries:int=100):self.max_entries=max_entries;self._items:list[ReflectionEntry]=[]
    def add(self,entry:ReflectionEntry)->None:
        for current in reversed(self._items[-20:]):
            if current.source==entry.source and current.code==entry.code:
                current.count+=1;current.summary=entry.summary;current.context=entry.context;return
        self._items.append(entry)
        if len(self._items)>self.max_entries:self._items=self._items[-self.max_entries:]
    def recent(self,limit:int=20)->list[dict[str,Any]]:
        return [{"source":e.source,"code":e.code,"summary":e.summary,"context":e.context,"count":e.count} for e in self._items[-limit:]]
