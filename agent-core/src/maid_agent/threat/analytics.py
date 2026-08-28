from __future__ import annotations

from collections import Counter
from math import atan2,degrees
from typing import Any

from maid_agent.memory.store import MemoryStore
from maid_agent.protocol.models import BridgeEvent,StateSnapshot


class ThreatAnalytics:
    def __init__(self,store:MemoryStore):
        self.store=store;self._windows:dict[str,dict[str,Any]]={};self._seen_hostiles:dict[str,set[str]]={}

    @staticmethod
    def _period(snapshot:StateSnapshot)->str:
        return "DAY" if 0<=snapshot.time_of_day<13000 else "NIGHT"

    @staticmethod
    def _direction(dx:float,dz:float)->str:
        angle=(degrees(atan2(dz,dx))+360)%360;names=["E","SE","S","SW","W","NW","N","NE"]
        return names[int((angle+22.5)//45)%8]

    def _window(self,day:int,period:str,tick:int)->dict[str,Any]:
        key=f"day-{day:05d}-{period.lower()}"
        row=self._windows.get(key)
        if row is None:
            persisted=self.store.load_threat_window(key)
            if persisted:
                row={**persisted,"entry_direction_histogram":Counter(persisted.get("entry_direction_histogram") or {}),
                    "attacker_types":Counter(persisted.get("attacker_types") or {}),"metadata":dict(persisted.get("metadata") or {})}
                seen={str(value) for value in row["metadata"].get("seen_hostile_ids",[]) if str(value)}
                self._seen_hostiles[key]=seen
            else:
                row={"window_id":key,"day":day,"period":period,"started_tick":tick,"ended_tick":tick,
                    "hostile_contacts":0,"unique_hostiles":0,"damage_taken":0.0,"deaths":0,"retreats":0,"base_damage_events":0,
                    "targeting_peak":0,"entry_direction_histogram":Counter(),"attacker_types":Counter(),"metadata":{}}
                self._seen_hostiles[key]=set()
            self._windows[key]=row
        self._seen_hostiles.setdefault(key,set());return row

    def ingest_snapshot(self,snapshot:StateSnapshot)->bool:
        period=self._period(snapshot);row=self._window(snapshot.day,period,snapshot.game_tick);key=row["window_id"]
        hostiles={e.uuid:e for e in snapshot.nearby_entities if e.category in {"HOSTILE","MONSTER","ENEMY"}}
        new=set(hostiles)-self._seen_hostiles[key]
        if new:
            row["hostile_contacts"]+=len(new)
            for entity_id in new:
                entity=hostiles[entity_id];self._seen_hostiles[key].add(entity_id)
                rel=entity.relative;row["entry_direction_histogram"][self._direction(float(rel.get("dx",0)),float(rel.get("dz",0)))]+=1
                row["attacker_types"][entity.type]+=1
        row["unique_hostiles"]=len(self._seen_hostiles[key]);row["targeting_peak"]=max(row["targeting_peak"],sum(1 for e in hostiles.values() if e.targeting_maid or e.recent_attacker));row["ended_tick"]=snapshot.game_tick
        self._persist(row)
        return len(new)>0 or row["targeting_peak"]>0

    def ingest_event(self,event:BridgeEvent,fallback_day:int)->bool:
        day=event.game_day if event.game_day is not None else fallback_day
        period=str(event.period or event.data.get("period") or "UNKNOWN")
        if period=="UNKNOWN" and event.time_of_day is not None:period="DAY" if 0<=event.time_of_day<13000 else "NIGHT"
        row=self._window(day,period,event.game_tick);kind=event.event_type;strategic=False
        if kind in {"HOSTILE_WAVE_DETECTED","HOSTILE_CONTACT","ENTITY_TARGETING_MAID"}:
            # Snapshot UUIDs are the sole contact counter. Events only trigger a
            # strategic review and carry high-signal context, preventing double count.
            count=int(event.data.get("count",1));strategic=kind=="HOSTILE_WAVE_DETECTED" or count>=4
            row["metadata"]["last_hostile_signal"]={"kind":kind,"count":count,"entity_uuid":event.data.get("entity_uuid"),"entity_type":event.data.get("entity_type")}
        elif kind=="DAMAGE_TAKEN":row["damage_taken"]+=float(event.data.get("amount",0));strategic=float(event.data.get("amount",0))>=4
        elif kind=="MAID_DEATH":row["deaths"]+=1;strategic=True
        elif kind in {"RETREAT","REFLEX_RETREAT"}:row["retreats"]+=1
        elif kind in {"BASE_DAMAGED","BLOCK_BROKEN_NEAR_BASE"}:row["base_damage_events"]+=1;strategic=True
        row["ended_tick"]=max(row["ended_tick"],event.game_tick);self._persist(row);return strategic

    def _persist(self,row:dict[str,Any])->None:
        row["metadata"]["seen_hostile_ids"]=sorted(self._seen_hostiles.get(row["window_id"],set()))
        payload={**row,"entry_direction_histogram":dict(row["entry_direction_histogram"]),"attacker_types":dict(row["attacker_types"])};self.store.upsert_threat_window(payload)

    def context_summary(self,current_day:int,limit:int=8)->dict[str,Any]:
        windows=self.store.recent_threat_windows(limit=limit);recent=[w for w in windows if w["day"]>=max(0,current_day-5)]
        total_contacts=sum(w["hostile_contacts"] for w in recent);damage=sum(float(w["damage_taken"]) for w in recent);deaths=sum(w["deaths"] for w in recent)
        directions=Counter();attackers=Counter()
        for w in recent:directions.update(w["entry_direction_histogram"]);attackers.update(w["attacker_types"])
        risk="CRITICAL" if deaths or damage>=20 else "HIGH" if total_contacts>=15 or damage>=8 else "MEDIUM" if total_contacts>=5 else "LOW"
        return {"risk_level":risk,"days_considered":5,"hostile_contacts":total_contacts,"damage_taken":damage,"deaths":deaths,
            "dominant_directions":directions.most_common(4),"attacker_types":attackers.most_common(8),"windows":recent}
