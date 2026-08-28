from __future__ import annotations

import asyncio,time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ControlEvent:
    type:str;payload:dict[str,Any];timestamp_ms:int


class EventBus:
    def __init__(self,history_size:int=500):self._subscribers:set[asyncio.Queue[ControlEvent]]=set();self._history:deque[ControlEvent]=deque(maxlen=history_size)
    def publish(self,event_type:str,payload:dict[str,Any])->None:
        event=ControlEvent(event_type,payload,int(time.time()*1000));self._history.append(event)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:queue.get_nowait()
                except asyncio.QueueEmpty:pass
            try:queue.put_nowait(event)
            except asyncio.QueueFull:pass
    def subscribe(self,maxsize:int=500)->asyncio.Queue[ControlEvent]:
        queue:asyncio.Queue[ControlEvent]=asyncio.Queue(maxsize=maxsize);self._subscribers.add(queue);return queue
    def unsubscribe(self,queue:asyncio.Queue[ControlEvent])->None:self._subscribers.discard(queue)
    def recent(self,limit:int=100)->list[dict[str,Any]]:
        return [{"type":e.type,"payload":e.payload,"timestamp_ms":e.timestamp_ms} for e in list(self._history)[-limit:]]
