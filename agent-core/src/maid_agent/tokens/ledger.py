from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from maid_agent.memory.store import MemoryStore

LedgerName=Literal["runtime","rnd"]


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens:int;completion_tokens:int;total_tokens:int;estimated:bool=False
    @classmethod
    def from_provider(cls,usage:dict|None,*,prompt_fallback:int=0,completion_fallback:int=0)->"TokenUsage":
        if usage:
            p=int(usage.get("prompt_tokens",usage.get("input_tokens",0)));c=int(usage.get("completion_tokens",usage.get("output_tokens",0)))
            return cls(p,c,int(usage.get("total_tokens",p+c)),False)
        return cls(prompt_fallback,completion_fallback,prompt_fallback+completion_fallback,True)


class TokenLedger:
    def __init__(self,store:MemoryStore):self.store=store
    def record(self,*,ledger:LedgerName,purpose:str,model:str,request_id:str,usage:TokenUsage,game_day:int|None=None,cycle_id:str|None=None)->None:
        with self.store.connection() as conn:conn.execute("""INSERT INTO token_usage(ledger,purpose,model,prompt_tokens,completion_tokens,total_tokens,request_id,estimated,game_day,cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (ledger,purpose,model,usage.prompt_tokens,usage.completion_tokens,usage.total_tokens,request_id,1 if usage.estimated else 0,game_day,cycle_id))
    def total(self,ledger:LedgerName,*,game_day:int|None=None,cycle_id:str|None=None,real_hour:bool=False)->int:
        query="SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE ledger=?";args:list[object]=[ledger]
        if game_day is not None:query+=" AND game_day=?";args.append(game_day)
        if cycle_id is not None:query+=" AND cycle_id=?";args.append(cycle_id)
        if real_hour:query+=" AND created_at>=datetime('now','-1 hour')"
        with self.store.connection() as conn:return int(conn.execute(query,args).fetchone()[0])
    def by_purpose(self,ledger:LedgerName,*,cycle_id:str|None=None)->dict[str,int]:
        query="SELECT purpose,COALESCE(SUM(total_tokens),0) total FROM token_usage WHERE ledger=?";args:list[object]=[ledger]
        if cycle_id is not None:query+=" AND cycle_id=?";args.append(cycle_id)
        query+=" GROUP BY purpose"
        with self.store.connection() as conn:rows=conn.execute(query,args).fetchall()
        return {str(r["purpose"]):int(r["total"]) for r in rows}
    def total_between_days(self,ledger:LedgerName,start_day:int,end_day:int)->int:
        with self.store.connection() as conn:
            return int(conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE ledger=? AND game_day>=? AND game_day<=?",
                (ledger,int(start_day),int(end_day)),
            ).fetchone()[0])
    def snapshot(self,*,current_day:int,rnd_budget:int,current_cycle_id:str|None=None,runtime_stage_start_day:int|None=None)->dict:
        rnd_used=self.total("rnd",cycle_id=current_cycle_id) if current_cycle_id else self.total("rnd")
        return {"runtime_today":self.total("runtime",game_day=current_day),"runtime_last_hour":self.total("runtime",real_hour=True),"runtime_total":self.total("runtime"),
            "runtime_current_stage":self.total_between_days("runtime",runtime_stage_start_day,current_day) if runtime_stage_start_day is not None else self.total("runtime",game_day=current_day),
            "runtime_stage_start_day":runtime_stage_start_day,
            "rnd_cycle_id":current_cycle_id,"rnd_used_current_cycle":rnd_used,"rnd_budget_current_cycle":rnd_budget,"rnd_remaining_current_cycle":max(0,rnd_budget-rnd_used),
            # Compatibility aliases for 0.1 clients; all values still use only the selected current cycle when supplied.
            "rnd_used":rnd_used,"rnd_budget":rnd_budget,"rnd_remaining":max(0,rnd_budget-rnd_used),
            "runtime_by_purpose":self.by_purpose("runtime"),"rnd_by_purpose_current_cycle":self.by_purpose("rnd",cycle_id=current_cycle_id) if current_cycle_id else self.by_purpose("rnd")}
