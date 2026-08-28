from __future__ import annotations

import json,os
from pathlib import Path
from typing import Literal
from pydantic import AliasChoices,BaseModel,Field,field_validator,model_validator


class ProviderProfile(BaseModel):
    profile_id:str;display_name:str;base_url:str;model:str
    api_key_secret_id:str=""
    chat_completions_path:str="/chat/completions"
    timeout_seconds:int=Field(120,ge=5,le=1800);max_retries:int=Field(3,ge=0,le=10)
    supports_json_schema:bool=True
    extra_headers:dict[str,str]=Field(default_factory=dict,exclude=True)
    @field_validator("base_url","model")
    @classmethod
    def non_empty(cls,v:str)->str:
        v=v.strip()
        if not v:raise ValueError("value must not be empty")
        return v
    @field_validator("chat_completions_path")
    @classmethod
    def path(cls,v:str)->str:
        v=v.strip() or "/chat/completions";return v if v.startswith("/") else "/"+v


class RuntimeBudgetSettings(BaseModel):
    enabled:bool=False
    max_per_game_day:int|None=Field(default=None,ge=1)
    max_per_real_hour:int|None=Field(default=None,ge=1)
    reserve_tokens:int=Field(4096,ge=0)


class RndBudgetSettings(BaseModel):
    budget_per_cycle:int=Field(100_000_000,ge=1)
    cycle_game_days:int=Field(5,ge=1,le=365)
    max_single_request:int=Field(2_000_000,ge=1024)


class AgentSettings(BaseModel):
    host:str="127.0.0.1";bridge_port:int=Field(8765,ge=1024,le=65535);control_port:int=Field(8766,ge=1024,le=65535)
    control_token:str="";data_dir:Path
    runtime_profile:ProviderProfile|None=None;rnd_profile:ProviderProfile|None=None
    runtime_budget:RuntimeBudgetSettings=Field(default_factory=RuntimeBudgetSettings);rnd_budget:RndBudgetSettings=Field(default_factory=RndBudgetSettings)
    autonomous_review_seconds:int=Field(90,ge=15,le=900);strict_survival:bool=True
    harness_runner_path:str|None=Field(default=None,validation_alias=AliasChoices("harness_runner_path","full_harness_runner_path"))
    harness_work_dir:Path|None=Field(default=None,validation_alias=AliasChoices("harness_work_dir","rnd_work_root"))
    source_workspace:Path|None=None
    owner_uuid:str|None=None;auto_start:bool=False
    setup_complete:bool=False;api_probes:dict=Field(default_factory=dict);minecraft_restart_required:bool=False
    log_level:Literal["DEBUG","INFO","WARNING","ERROR"]="INFO"

    @model_validator(mode="after")
    def ports_distinct(self)->"AgentSettings":
        if self.bridge_port==self.control_port:raise ValueError("bridge_port and control_port must differ")
        return self

    @property
    def database_path(self)->Path:return self.data_dir/"state"/"maid_agent.sqlite3"
    @classmethod
    def default_data_dir(cls)->Path:
        base=Path(os.environ.get("APPDATA",Path.home()/"AppData"/"Roaming")) if os.name=="nt" else Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))
        return base/"MaidAI"
    @classmethod
    def load(cls,path:Path|None=None,*,control_token:str="")->"AgentSettings":
        path=path or cls.default_data_dir()/"config"/"agent.json";raw=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"data_dir":str(cls.default_data_dir())}
        if control_token:raw["control_token"]=control_token
        settings=cls.model_validate(raw)
        for part in ("state","logs","handoff","backups","diagnostics","blueprints","rnd-worktrees","source-workspace"):(settings.data_dir/part).mkdir(parents=True,exist_ok=True)
        return settings
