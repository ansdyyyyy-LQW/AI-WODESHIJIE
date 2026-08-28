from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.models import RndCycle,RndMode


class RndCycleConflict(RuntimeError):
    code = "RND_CYCLE_ALREADY_ACTIVE"


class RndTrigger:
    def __init__(self,store:MemoryStore,handoff_root:Path,*,cycle_days:int=5,token_budget:int=100_000_000,budget:int|None=None):
        self.store=store;self.handoff_root=Path(handoff_root);self.cycle_days=cycle_days;self.token_budget=int(budget if budget is not None else token_budget)
        self.handoff_root.mkdir(parents=True,exist_ok=True)

    @property
    def budget(self)->int:
        return self.token_budget

    def create_if_due(self,current_day:int)->RndCycle|None:
        if current_day<=0 or current_day%self.cycle_days!=0:return None
        return self._create_new(current_day)

    def create(self,trigger_day:int)->RndCycle:
        """Create one new cycle, never return an existing cycle as if it were new."""
        cycle=self._create_new(trigger_day)
        if cycle is None:
            raise RndCycleConflict("已有研发周期正在运行，或当前游戏日已经创建过研发周期")
        return cycle

    def _create_new(self,trigger_day:int)->RndCycle|None:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM rnd_cycles WHERE status IN ('CREATED','RUNNING','SUSPENDED') LIMIT 1"
            ).fetchone():
                return None
            if conn.execute("SELECT 1 FROM rnd_cycles WHERE trigger_day=?",(trigger_day,)).fetchone():
                return None
            sequence=0
            for row in conn.execute("SELECT cycle_id FROM rnd_cycles"):
                match=re.fullmatch(r"cycle-(\d+)",str(row["cycle_id"]))
                if match:sequence=max(sequence,int(match.group(1)))
            sequence+=1
            cycle_id=f"cycle-{sequence:03d}"
            artifact_dir=self.handoff_root/cycle_id
            while artifact_dir.exists():
                sequence+=1;cycle_id=f"cycle-{sequence:03d}";artifact_dir=self.handoff_root/cycle_id
            previous_end=conn.execute(
                "SELECT MAX(runtime_period_end_day) FROM rnd_cycles"
            ).fetchone()[0]
            period_start=(int(previous_end)+1) if previous_end is not None else max(0,trigger_day-self.cycle_days)
            period_end=trigger_day-1
            if period_start>period_end:
                return None
            cycle=RndCycle(cycle_id,trigger_day,period_start,period_end,self.token_budget,artifact_dir)
            try:
                conn.execute("""INSERT INTO rnd_cycles(cycle_id,trigger_day,runtime_period_start_day,runtime_period_end_day,token_budget,status,mode,artifact_dir) VALUES(?,?,?,?,?,?,?,?)""",
                    (cycle.cycle_id,cycle.trigger_day,cycle.period_start_day,cycle.period_end_day,cycle.token_budget,cycle.status,cycle.mode,str(cycle.artifact_dir)))
            except sqlite3.IntegrityError:
                return None
        try:
            artifact_dir.mkdir(parents=True,exist_ok=False)
        except FileExistsError:
            with self.store.connection() as conn:
                conn.execute(
                    "UPDATE rnd_cycles SET status='FAILED', outcome='FAILED', "
                    "summary='研发目录已经存在，为避免并发覆盖已停止启动' WHERE cycle_id=?",
                    (cycle.cycle_id,),
                )
            raise RndCycleConflict("研发目录已经存在，已阻止重复启动")
        except OSError:
            with self.store.connection() as conn:
                conn.execute(
                    "UPDATE rnd_cycles SET status='FAILED', outcome='FAILED', "
                    "summary='无法建立独立研发目录' WHERE cycle_id=?",
                    (cycle.cycle_id,),
                )
            raise
        return cycle

    def list_cycles(self,limit:int=50)->list[dict]:
        with self.store.connection() as conn:
            rows=conn.execute("SELECT * FROM rnd_cycles ORDER BY trigger_day DESC,created_at DESC LIMIT ?",(limit,)).fetchall()
            result=[]
            for row in rows:
                item=dict(row)
                for key in ("budget_plan_json","checkpoint_json","project_state_json","failure_state_json","dsh_phase_progress_json"):
                    try:item[key.removesuffix("_json")]=json.loads(item.pop(key) or "{}")
                    except (TypeError,json.JSONDecodeError):item[key.removesuffix("_json")]={}
                used=int(conn.execute("SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE ledger='rnd' AND cycle_id=?",(item["cycle_id"],)).fetchone()[0])
                item["used_tokens"]=used;item["remaining_tokens"]=max(0,int(item["token_budget"])-used)
                artifact_root=Path(str(item.get("artifact_dir") or ""))
                try:
                    artifact_root=artifact_root.resolve()
                    artifact_root.relative_to(self.handoff_root.resolve())
                except (OSError,ValueError):
                    artifact_root=None
                manifest=self._read_artifact_json(artifact_root,"handoff_manifest.json")
                rnd_result=self._read_artifact_json(artifact_root,"output/rnd_result.json")
                item["result"]=rnd_result
                item["final_validator"]=dict(rnd_result.get("final_validator") or {})
                item["artifacts"]=list(manifest.get("artifacts") or [])
                item["artifact_count"]=len(item["artifacts"])
                result.append(item)
            return result

    @staticmethod
    def _read_artifact_json(root:Path|None,relative:str)->dict:
        if root is None:return {}
        try:
            target=(root/relative).resolve();target.relative_to(root)
            value=json.loads(target.read_text(encoding="utf-8"))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,json.JSONDecodeError):
            return {}
    def list(self)->list[dict]:
        return self.list_cycles()

    def update(self,cycle_id:str,*,status:str,summary:str="",mode:str|None=None)->None:
        fields=["status=?","summary=?","updated_at=CURRENT_TIMESTAMP"];args:list[object]=[status,summary]
        if mode is not None:
            fields.append("mode=?");args.append(mode)
        args.append(cycle_id)
        with self.store.connection() as conn:
            conn.execute(f"UPDATE rnd_cycles SET {','.join(fields)} WHERE cycle_id=?",args)
