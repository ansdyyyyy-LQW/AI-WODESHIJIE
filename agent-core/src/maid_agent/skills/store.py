from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from maid_agent.actions.catalog import CATALOG
from maid_agent.memory.store import MemoryStore
from maid_agent.skills.models import SkillSpec,SkillStats


class SkillStore:
    def __init__(self,store:MemoryStore,failure_refinement_threshold:int=3):self.store=store;self.failure_refinement_threshold=failure_refinement_threshold

    def put(self,spec:SkillSpec,source_path:str|None=None)->None:
        with self.store.connection() as conn:
            conn.execute("""INSERT INTO skills(skill_id,version,name,kind,spec_json,source_path,status,created_by,goal_tags_json)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(skill_id,version) DO UPDATE SET name=excluded.name,kind=excluded.kind,spec_json=excluded.spec_json,
              source_path=excluded.source_path,status=excluded.status,created_by=excluded.created_by,goal_tags_json=excluded.goal_tags_json""",
              (spec.skill_id,spec.version,spec.name,spec.kind,spec.model_dump_json(),source_path,spec.status,spec.created_by,json.dumps(spec.goal_tags,ensure_ascii=False)))

    def get(self,skill_id:str,version:int|None=None,*,production_only:bool=False)->SkillSpec|None:
        query="SELECT * FROM skills WHERE skill_id=?";args:list[Any]=[skill_id]
        if version and version>0:query+=" AND version=?";args.append(version)
        if production_only:query+=" AND status='ACTIVE'"
        query+=" ORDER BY version DESC LIMIT 1"
        with self.store.connection() as conn:row=conn.execute(query,args).fetchone()
        return SkillSpec.model_validate_json(row["spec_json"]) if row else None

    def set_status(self,skill_id:str,version:int,status:str)->bool:
        if status not in {"ACTIVE","CANDIDATE","DISABLED","RETIRED"}:raise ValueError("invalid skill status")
        with self.store.connection() as conn:
            row=conn.execute("SELECT spec_json,source_path FROM skills WHERE skill_id=? AND version=?",(skill_id,version)).fetchone()
            if row is None:return False
            spec=SkillSpec.model_validate_json(row["spec_json"])
            if status=="ACTIVE":self.validate_candidate(spec,source_path=row["source_path"])
            spec.status=status
            cursor=conn.execute("UPDATE skills SET status=?,spec_json=? WHERE skill_id=? AND version=?",(status,spec.model_dump_json(),skill_id,version))
        return cursor.rowcount>0

    @staticmethod
    def validate_candidate(spec:SkillSpec,*,source_path:str|None=None)->None:
        """Bounded production validation before a generated DSL can become ACTIVE."""
        if spec.kind!="dsl":raise ValueError("代码型 Candidate 不能直接进入 Runtime")
        allowed_conditions={"ITEM_COUNT","TAG_COUNT","HEALTH_AT_LEAST","HUNGER_AT_LEAST","POSITION_WITHIN","NO_HOSTILE_WITHIN","ACTION_CODE","WORLD_DELTA","INVENTORY_DELTA","BLOCK_STATE","CUSTOM"}
        has_verified_effect=bool(spec.success)
        for step in spec.steps:
            if step.tool=="run_skill":raise ValueError("Candidate Skill 禁止递归调用 run_skill")
            contract=CATALOG.get(step.tool)
            CATALOG.validate(step.tool,step.args,allow_templates=True)
            for condition in [*step.preconditions,*step.success_conditions]:
                if condition.type not in allowed_conditions:raise ValueError(f"不支持的后置条件：{condition.type}")
            if contract.side_effect and step.success_conditions:has_verified_effect=True
        for condition in spec.success:
            if condition.type not in allowed_conditions:raise ValueError(f"不支持的 Skill 后置条件：{condition.type}")
        if spec.created_by=="rnd" and not has_verified_effect:
            raise ValueError("R&D Candidate 至少需要一个可验证的执行后置条件")
        if spec.created_by=="rnd":
            if not source_path:raise ValueError("R&D Candidate 缺少 Harness 来源")
            result_path=Path(source_path).resolve().parent.parent/"harness_result.json"
            try:result=json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError,json.JSONDecodeError) as exc:raise ValueError("R&D Candidate 缺少可读的 Harness 结果") from exc
            mode=str(result.get("mode") or "")
            if not (result.get("ok") is True and mode in {"FULL_HARNESS","RndMode.FULL_HARNESS"}):
                raise ValueError("R&D Candidate 未通过 FULL_HARNESS，禁止激活")

    def list(self,*,status:str|None="ACTIVE",limit:int=500)->list[dict[str,Any]]:
        query="SELECT * FROM skills";args:list[Any]=[]
        if status is not None:query+=" WHERE status=?";args.append(status)
        query+=" ORDER BY name,version DESC LIMIT ?";args.append(limit)
        with self.store.connection() as conn:rows=conn.execute(query,args).fetchall()
        result=[]
        for r in rows:
            total=int(r["success_count"])+int(r["failure_count"]);codes=json.loads(r["failure_codes_json"] or "{}")
            result.append({"skill_id":r["skill_id"],"name":r["name"],"version":r["version"],"kind":r["kind"],"goal_tags":json.loads(r["goal_tags_json"] or "[]"),
              "success_count":r["success_count"],"failure_count":r["failure_count"],"consecutive_failures":r["consecutive_failures"],
              "success_rate":(r["success_count"]/total if total else None),"rank_score":(r["success_count"]+1)/(total+2)-.08*r["consecutive_failures"],
              "avg_duration":r["avg_duration"],"status":r["status"],"created_by":r["created_by"],"failure_codes":codes,"last_failure_code":r["last_failure_code"]})
        return result

    def list_active_skills(self,limit:int=20)->list[dict[str,Any]]:return sorted(self.list(status="ACTIVE"),key=lambda x:x["rank_score"],reverse=True)[:limit]

    def rank_for_goal(self,goal:Any,*,context:dict[str,Any]|None=None,limit:int=10)->list[dict[str,Any]]:
        rows=self.list(status="ACTIVE");text=(getattr(goal,"objective","")+" "+str(getattr(goal,"type",""))).lower() if goal else ""
        for row in rows:
            tags=[str(tag).lower() for tag in row.get("goal_tags",[])];match=sum(1 for tag in tags if tag and tag in text)
            row["goal_match_score"]=match*1.5+row["rank_score"]
        return sorted(rows,key=lambda r:(r["goal_match_score"],r["success_count"]),reverse=True)[:limit]

    def record(self,skill_id:str,version:int,*,success:bool,duration:float,code:str,context:dict[str,Any]|None=None)->None:
        with self.store.connection() as conn:
            row=conn.execute("SELECT * FROM skills WHERE skill_id=? AND version=?",(skill_id,version)).fetchone()
            if not row:return
            successes=int(row["success_count"]);failures=int(row["failure_count"]);n=successes+failures;avg=((float(row["avg_duration"])*n)+duration)/(n+1)
            codes=json.loads(row["failure_codes_json"] or "{}");consecutive=0 if success else int(row["consecutive_failures"])+1
            if not success:codes[code]=codes.get(code,0)+1
            conn.execute("""UPDATE skills SET success_count=?,failure_count=?,consecutive_failures=?,avg_duration=?,failure_codes_json=?,last_used_at=CURRENT_TIMESTAMP,last_failure_code=? WHERE skill_id=? AND version=?""",
                (successes+(1 if success else 0),failures+(0 if success else 1),consecutive,avg,json.dumps(codes),None if success else code,skill_id,version))
            if not success and consecutive>=self.failure_refinement_threshold:
                conn.execute("""INSERT OR IGNORE INTO skill_refinement_queue(skill_id,version,reason,context_json,status) VALUES(?,?,?,?, 'OPEN')""",
                    (skill_id,version,f"consecutive_failures={consecutive}; last_code={code}",json.dumps(context or {},ensure_ascii=False,default=str)))

    def refinement_queue(self,status:str="OPEN",limit:int=100)->list[dict[str,Any]]:
        with self.store.connection() as conn:rows=conn.execute("SELECT * FROM skill_refinement_queue WHERE status=? ORDER BY created_at DESC LIMIT ?",(status,limit)).fetchall()
        result=[]
        for row in rows:item=dict(row);item["context"]=json.loads(item.pop("context_json") or "{}");result.append(item)
        return result
