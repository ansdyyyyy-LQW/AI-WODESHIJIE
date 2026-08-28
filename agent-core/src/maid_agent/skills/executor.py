from __future__ import annotations

import time
from typing import Any
from uuid import uuid5,NAMESPACE_URL

from maid_agent.actions.catalog import CATALOG,ToolValidationError
from maid_agent.actions.client import ActionClient
from maid_agent.goal.postconditions import PostconditionVerifier
from maid_agent.protocol.models import ActionResult,StateSnapshot
from maid_agent.skills.models import SkillSpec
from maid_agent.skills.store import SkillStore


def _resolve(value:Any,context:dict[str,Any])->Any:
    if isinstance(value,str) and value.startswith("$"):
        current:Any=context
        for part in value[1:].split("."):
            if isinstance(current,dict) and part in current:current=current[part]
            elif isinstance(current,list) and part.isdigit() and int(part)<len(current):current=current[int(part)]
            else:raise ValueError(f"unresolved skill reference: {value}")
        return current
    if isinstance(value,dict):return {k:_resolve(v,context) for k,v in value.items()}
    if isinstance(value,list):return [_resolve(v,context) for v in value]
    return value


class SkillExecutor:
    def __init__(self,client:ActionClient,store:SkillStore,verifier:PostconditionVerifier):self.client=client;self.store=store;self.verifier=verifier

    @staticmethod
    def _validate_parameters(spec:SkillSpec,parameters:dict[str,Any])->dict[str,Any]:
        unknown=set(parameters)-set(spec.parameters)
        if unknown:raise ToolValidationError("INVALID_SKILL_PARAMETERS",f"未知 Skill 参数：{', '.join(sorted(unknown))}")
        missing=set(spec.parameters)-set(parameters)
        if missing:raise ToolValidationError("INVALID_SKILL_PARAMETERS",f"缺少 Skill 参数：{', '.join(sorted(missing))}")
        normalized={}
        for name,kind in spec.parameters.items():
            value=parameters[name]
            if kind in {"str","string","item_id","uuid"} and not isinstance(value,str):raise ToolValidationError("INVALID_SKILL_PARAMETERS",f"{name} 必须是文本")
            if kind in {"int","integer"} and (not isinstance(value,int) or isinstance(value,bool)):raise ToolValidationError("INVALID_SKILL_PARAMETERS",f"{name} 必须是整数")
            if kind in {"float","number"} and (not isinstance(value,(int,float)) or isinstance(value,bool)):raise ToolValidationError("INVALID_SKILL_PARAMETERS",f"{name} 必须是数字")
            normalized[name]=value
        return normalized

    async def execute(self,spec:SkillSpec,parameters:dict[str,Any],snapshot_getter)->tuple[list[ActionResult],bool,str]:
        if spec.status!="ACTIVE":raise ToolValidationError("SKILL_NOT_ACTIVE","只有 ACTIVE Skill 可以进入生产运行链")
        parameters=self._validate_parameters(spec,parameters)
        started=time.monotonic();context={"params":parameters};results:list[ActionResult]=[];success=False;code="EMPTY"
        try:
            for index,step in enumerate(spec.steps):
                snapshot:StateSnapshot|None=snapshot_getter()
                if snapshot is None:code="NO_SNAPSHOT";return results,False,code
                if step.preconditions and not self.verifier.all(step.preconditions,snapshot):code="PRECONDITION_FAILED";return results,False,code
                args=_resolve(step.args,{**context,"previous":results[-1].data if results else {},"results":[r.data for r in results]})
                args=CATALOG.validate(step.tool,args)
                result:ActionResult|None=None
                for attempt in range(step.max_retries+1):
                    request_id=str(uuid5(NAMESPACE_URL,f"skill:{spec.skill_id}:{spec.version}:{index}:{attempt}:{args}"))
                    result=await self.client.execute(step.tool,args,timeout_ticks=step.timeout_ticks,request_id=request_id)
                    if result.ok:
                        latest=snapshot_getter() or snapshot
                        if not step.success_conditions or self.verifier.all(step.success_conditions,latest,result):break
                    if attempt>=step.max_retries:break
                assert result is not None;results.append(result)
                if not result.ok:code=result.code;return results,False,code
                latest=snapshot_getter() or snapshot
                if step.success_conditions and not self.verifier.all(step.success_conditions,latest,result):code="POSTCONDITION_FAILED";return results,False,code
            final=snapshot_getter()
            if spec.success and (final is None or not self.verifier.all(spec.success,final,results[-1] if results else None)):
                code="SKILL_POSTCONDITION_FAILED";return results,False,code
            success=True;code="SUCCESS";return results,True,code
        finally:
            self.store.record(spec.skill_id,spec.version,success=success,duration=time.monotonic()-started,code=code,context={"parameters":parameters,"result_codes":[r.code for r in results]})
