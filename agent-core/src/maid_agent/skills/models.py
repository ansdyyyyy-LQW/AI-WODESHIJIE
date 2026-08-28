from __future__ import annotations

from typing import Any,Literal
from uuid import uuid4
from pydantic import BaseModel,Field,model_validator
from maid_agent.goal.models import Condition


class SkillStep(BaseModel):
    tool:str
    args:dict[str,Any]=Field(default_factory=dict)
    preconditions:list[Condition]=Field(default_factory=list)
    success_conditions:list[Condition]=Field(default_factory=list)
    timeout_ticks:int=Field(1200,ge=1,le=72000)
    max_retries:int=Field(1,ge=0,le=3)


class SkillSpec(BaseModel):
    skill_id:str=Field(default_factory=lambda:str(uuid4()))
    name:str
    version:int=Field(1,ge=1)
    kind:Literal["dsl","code_candidate"]="dsl"
    description:str=""
    goal_tags:list[str]=Field(default_factory=list)
    parameters:dict[str,str]=Field(default_factory=dict)
    required_capabilities:list[str]=Field(default_factory=list)
    steps:list[SkillStep]
    success:list[Condition]=Field(default_factory=list)
    created_by:Literal["builtin","generated","rnd","user"]="builtin"
    status:Literal["ACTIVE","CANDIDATE","DISABLED","RETIRED"]="ACTIVE"

    @model_validator(mode="after")
    def non_empty(self)->"SkillSpec":
        if not self.steps:raise ValueError("skill must contain at least one step")
        return self


class SkillStats(BaseModel):
    skill_id:str;version:int;success_count:int=0;failure_count:int=0;consecutive_failures:int=0;avg_duration:float=0;failure_codes:dict[str,int]=Field(default_factory=dict)
    @property
    def rank_score(self)->float:
        reliability=(self.success_count+1)/(self.success_count+self.failure_count+2)
        return max(0.0,reliability-.08*self.consecutive_failures)
