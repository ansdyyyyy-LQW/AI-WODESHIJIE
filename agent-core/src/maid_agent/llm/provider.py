from __future__ import annotations

from dataclasses import dataclass,field
from typing import Any,Protocol


@dataclass
class LLMResponse:
    content:str;tool_calls:list[dict[str,Any]]=field(default_factory=list);usage:dict[str,int]|None=None
    request_id:str="";model:str="";raw:dict[str,Any]=field(default_factory=dict)


class LLMProvider(Protocol):
    async def complete(self,messages:list[dict[str,str]],tools:list[dict[str,Any]]|None=None,*,model_role:str,purpose:str="planning",response_schema:dict[str,Any]|None=None)->LLMResponse:...
