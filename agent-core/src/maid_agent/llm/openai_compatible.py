from __future__ import annotations

import asyncio,json,time
from typing import Any,Callable,Literal
from uuid import uuid4
import httpx

from maid_agent.config import ProviderProfile
from maid_agent.llm.provider import LLMResponse
from maid_agent.memory.store import MemoryStore
from maid_agent.tokens.budget_guard import BudgetExceeded,BudgetGuard
from maid_agent.tokens.ledger import TokenLedger,TokenUsage


def join_url(base_url:str,path:str)->str:return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def estimate_tokens(messages:list[dict[str,str]],tools:list[dict[str,Any]]|None=None,response_schema:dict[str,Any]|None=None)->int:
    # UTF-8 byte count is intentionally conservative: a tokenizer cannot emit more
    # ordinary text tokens than the bytes represented, and the fixed margin covers wrappers.
    raw=json.dumps({"messages":messages,"tools":tools,"schema":response_schema},ensure_ascii=False,separators=(",",":"),default=str)
    return max(1,len(raw.encode("utf-8"))+256)


class ProviderError(RuntimeError):
    def __init__(self,message:str,*,status_code:int|None=None,retryable:bool=False,code:str="PROVIDER_ERROR"):
        super().__init__(message);self.status_code=status_code;self.retryable=retryable;self.code=code


class OpenAICompatibleProvider:
    def __init__(self,profile:ProviderProfile,api_key:str,ledger:TokenLedger,*,ledger_name:Literal["runtime","rnd"],
                 budget_guard:BudgetGuard|None=None,game_day_getter:Callable[[],int|None]=lambda:None,
                 cycle_id_getter:Callable[[],str|None]=lambda:None,transport:httpx.AsyncBaseTransport|None=None,
                 store:MemoryStore|None=None):
        self.profile=profile;self.api_key=api_key;self.ledger=ledger;self.ledger_name=ledger_name;self.budget_guard=budget_guard
        self.game_day_getter=game_day_getter;self.cycle_id_getter=cycle_id_getter;self.transport=transport;self.store=store or ledger.store

    async def complete(self,messages:list[dict[str,str]],tools:list[dict[str,Any]]|None=None,*,model_role:str,purpose:str="planning",response_schema:dict[str,Any]|None=None)->LLMResponse:
        request_id=str(uuid4());prompt_estimate=estimate_tokens(messages,tools,response_schema);estimated_request=prompt_estimate+4096
        day=self.game_day_getter();cycle_id=self.cycle_id_getter()
        completion_limit: int | None = None
        if self.budget_guard:
            if self.ledger_name=="runtime":self.budget_guard.check_runtime(game_day=day,estimated_request_tokens=estimated_request)
            else:
                _,completion_limit=self.budget_guard.limit_rnd_request(
                    cycle_id=cycle_id,prompt_tokens=prompt_estimate,purpose=purpose,desired_completion_tokens=4096
                )
        payload:dict[str,Any]={"model":self.profile.model,"messages":messages,"temperature":0.2}
        if completion_limit is not None:payload["max_tokens"]=completion_limit
        if tools:payload["tools"]=tools;payload["tool_choice"]="auto"
        if response_schema and self.profile.supports_json_schema:
            payload["response_format"]={"type":"json_schema","json_schema":{"name":model_role.replace("-","_")[:64],"strict":True,"schema":response_schema}}
        headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json",**self.profile.extra_headers}
        url=join_url(self.profile.base_url,self.profile.chat_completions_path);last_error:Exception|None=None;started=time.monotonic();http_status=None
        for attempt in range(self.profile.max_retries+1):
            try:
                async with httpx.AsyncClient(timeout=self.profile.timeout_seconds,transport=self.transport) as client:response=await client.post(url,headers=headers,json=payload)
                http_status=response.status_code
                if response.status_code>=400:
                    retryable=response.status_code in {408,409,425,429,500,502,503,504};text=response.text[:2000]
                    # Some compatible endpoints do not support json_schema. Retry once without it, while preserving model/base URL.
                    if response.status_code in {400,404,422} and "response_format" in payload and attempt<self.profile.max_retries:
                        payload.pop("response_format",None);continue
                    raise ProviderError(f"HTTP {response.status_code}: {text}",status_code=response.status_code,retryable=retryable,code=f"HTTP_{response.status_code}")
                data=response.json();choices=data.get("choices") or []
                if not choices:raise ProviderError("provider returned no choices",code="NO_CHOICES")
                message=choices[0].get("message") or {};content=message.get("content") or ""
                usage=TokenUsage.from_provider(data.get("usage"),prompt_fallback=prompt_estimate,completion_fallback=max(1,(len(content)+3)//4))
                final_id=str(data.get("id") or request_id)
                self.ledger.record(ledger=self.ledger_name,purpose=purpose,model=self.profile.model,request_id=final_id,usage=usage,game_day=day,cycle_id=cycle_id)
                self.store.record_llm_request(request_id=final_id,ledger=self.ledger_name,purpose=purpose,model=self.profile.model,http_status=response.status_code,ok=True,
                    latency_ms=int((time.monotonic()-started)*1000),prompt_tokens=usage.prompt_tokens,completion_tokens=usage.completion_tokens,total_tokens=usage.total_tokens,
                    estimated=usage.estimated,error_code="",cycle_id=cycle_id,game_day=day)
                if self.ledger_name=="rnd" and self.budget_guard and cycle_id:
                    after=self.budget_guard.rnd_checkpoint(cycle_id=cycle_id)
                    if after.used>after.budget:
                        raise BudgetExceeded("RND_PROVIDER_OVERRAN_CAP","模型返回量超过服务端请求上限，周期已被硬停止")
                return LLMResponse(content=content,tool_calls=message.get("tool_calls") or [],usage=data.get("usage"),request_id=final_id,model=self.profile.model,raw=data)
            except (httpx.HTTPError,ProviderError,ValueError,json.JSONDecodeError) as exc:
                last_error=exc;retryable=isinstance(exc,httpx.HTTPError) or (isinstance(exc,ProviderError) and exc.retryable)
                if attempt>=self.profile.max_retries or not retryable:break
                await asyncio.sleep(min(8,2**attempt))
        code=getattr(last_error,"code",last_error.__class__.__name__ if last_error else "UNKNOWN")
        self.store.record_llm_request(request_id=request_id,ledger=self.ledger_name,purpose=purpose,model=self.profile.model,http_status=http_status,ok=False,
            latency_ms=int((time.monotonic()-started)*1000),prompt_tokens=prompt_estimate,completion_tokens=0,total_tokens=0,estimated=True,error_code=str(code),cycle_id=cycle_id,game_day=day)
        raise ProviderError(f"request failed: {last_error}",status_code=http_status,code=str(code)) from last_error
