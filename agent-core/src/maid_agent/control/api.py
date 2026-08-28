from __future__ import annotations

import asyncio,hashlib,hmac,json,logging,os,platform,re,shutil,sys,time,zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4
from websockets.asyncio.server import ServerConnection,serve

from maid_agent.brain.autonomous_loop import RuntimeController
from maid_agent.config import AgentSettings
from maid_agent.control.events import EventBus
from maid_agent.goal.models import Condition,PlanStep
from maid_agent.memory.store import MemoryStore
from maid_agent.rnd.mod_research.service import ModResearchService
from maid_agent.rnd.trigger import RndTrigger
from maid_agent.skills.store import SkillStore
from maid_agent.tokens.ledger import TokenLedger
from maid_agent.transport.ws_server import BridgeGateway
from maid_agent.protocol.models import MessageType

log=logging.getLogger(__name__)


def forge_47_supported(value: object) -> bool:
    """MaidAI targets the Forge 47.x line for Minecraft 1.20.1, not one patch only."""
    return re.fullmatch(r"47\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?",str(value or "").strip()) is not None


class ControlApi:
    def __init__(self,host:str,port:int,token:str,runtime:RuntimeController,event_bus:EventBus,store:MemoryStore,skills:SkillStore,
                 ledger:TokenLedger,gateway:BridgeGateway,rnd_trigger:RndTrigger,settings:AgentSettings,
                 shutdown_event:asyncio.Event|None=None):
        self.host=host;self.port=port;self.token=token;self.runtime=runtime;self.event_bus=event_bus;self.store=store;self.skills=skills;self.ledger=ledger;self.gateway=gateway;self.rnd_trigger=rnd_trigger;self.settings=settings
        self.mod_research=ModResearchService();self.shutdown_event=shutdown_event;self._server=None;self._instance_id=str(uuid4())

    async def start(self)->None:
        self._server=await serve(self._handler,self.host,self.port,ping_interval=20,max_size=16*1024*1024);log.info("Control API listening on %s:%s",self.host,self.port)
    async def close(self)->None:
        if self._server:self._server.close();await self._server.wait_closed()

    async def _handler(self,ws:ServerConnection)->None:
        forwarder:asyncio.Task|None=None
        queue:asyncio.Queue|None=None
        try:
            async for raw in ws:
                try:
                    request=json.loads(raw);request_id=str(request.get("request_id") or uuid4());command=str(request.get("command") or "").upper();args=request.get("args") or {};provided=str(request.get("token") or "")
                    authorized=(not self.token) or hmac.compare_digest(provided,self.token)
                    if not authorized:
                        response={"type":"CONTROL_RESULT","request_id":request_id,"ok":False,"code":"UNAUTHORIZED","message":"控制令牌无效","data":{}}
                    else:
                        if forwarder is None:
                            queue=self.event_bus.subscribe();forwarder=asyncio.create_task(self._forward_events(ws,queue))
                        try:data=await self._dispatch(command,args);response={"type":"CONTROL_RESULT","request_id":request_id,"ok":True,"code":"OK","message":"","data":data}
                        except Exception as exc:response={"type":"CONTROL_RESULT","request_id":request_id,"ok":False,"code":getattr(exc,"code",exc.__class__.__name__),"message":str(exc),"data":{}}
                except Exception as exc:response={"type":"CONTROL_RESULT","request_id":"","ok":False,"code":"INVALID_REQUEST","message":str(exc),"data":{}}
                await ws.send(json.dumps(response,ensure_ascii=False,default=str))
        finally:
            if forwarder:
                forwarder.cancel()
                with suppress(asyncio.CancelledError):await forwarder
            if queue is not None:self.event_bus.unsubscribe(queue)

    async def _forward_events(self,ws:ServerConnection,queue:asyncio.Queue)->None:
        while True:
            event=await queue.get();await ws.send(json.dumps({"type":"EVENT","event":event.type,"timestamp_ms":event.timestamp_ms,"payload":event.payload},ensure_ascii=False,default=str))

    async def _dispatch(self,command:str,args:dict[str,Any])->dict[str,Any]:
        if command in {"GET_STATUS","STATUS"}:return self._status()
        if command=="START":
            gate=self._start_gate()
            if not gate["ready"]:raise RuntimeError("启动条件未满足："+"；".join(gate["missing"]))
            await self.runtime.start();return self._status()
        if command=="PAUSE":await self.runtime.pause();return self._status()
        if command=="RESUME":await self.runtime.resume();return self._status()
        if command=="STOP":await self.runtime.stop();return self._status()
        if command=="SHUTDOWN":
            if self.shutdown_event is None:raise RuntimeError("当前 Agent 不允许远程关闭进程")
            await self.runtime.stop()
            # Give this authenticated response time to reach Control Center before
            # the outer run loop closes the WebSocket servers and SQLite handles.
            asyncio.get_running_loop().call_later(.25,self.shutdown_event.set)
            return {"shutting_down":True,"instance_id":self._instance_id}
        if command=="REQUEST_REVIEW":self.runtime.request_strategic_review();return {"requested":True}
        if command=="CREATE_GOAL":return self.runtime.submit_user_goal(str(args.get("objective") or ""),int(args.get("priority",70)),args.get("success_conditions"),args.get("steps"))
        if command=="GET_MEMORY":
            snap=self.gateway.latest_snapshot;near=snap.position.model_dump() if snap else None;dim=snap.dimension if snap else None
            return {"summary":self.store.summary(),"events":self.store.recent_events(limit=int(args.get("event_limit",200))),"locations":self.store.recall_locations(dimension=dim,near=near,limit=100),"resources":self.store.recall_resources(dimension=dim,near=near,limit=200),"structures":self.store.recall_structures(dimension=dim,limit=100)}
        if command=="GET_EVENTS":return {"events":self.store.recent_events(limit=min(int(args.get("limit",200)),2000),min_day=args.get("min_day"),types=args.get("types"))}
        if command=="GET_SKILLS":return {"skills":self.skills.list(status=args.get("status") if "status" in args else None,limit=1000),"refinement_queue":self.skills.refinement_queue(limit=500)}
        if command=="SET_SKILL_STATUS":
            status=str(args["status"]).upper();ok=self.skills.set_status(str(args["skill_id"]),int(args["version"]),status);return {"updated":ok,"status":status}
        if command=="GET_THREAT":return self.runtime.threats.context_summary(self.runtime.current_day or 0)|{"windows":self.store.recent_threat_windows(limit=100)}
        if command=="GET_BUILDING":return {"builds":self.store.list_build_checkpoints(limit=200)}
        if command in {"EXECUTE_BLUEPRINT","EXECUTE_BUILDING_DSL"}:
            was_running=bool(self.runtime._desired_running)
            if was_running:await self.runtime.pause()
            try:
                if command=="EXECUTE_BLUEPRINT":result=self.runtime.queue_blueprint(dict(args["blueprint"]),dict(args["origin"]),int(args.get("rotation",0)),int(args.get("priority",75)))
                else:result=self.runtime.queue_building_dsl(dict(args["dsl"]),dict(args["origin"]),int(args.get("rotation",0)),int(args.get("priority",75)))
            finally:
                if was_running:await self.runtime.resume()
            return result
        if command=="GET_TOKENS":
            cycles=self.rnd_trigger.list_cycles(limit=1);cycle_id=cycles[0]["cycle_id"] if cycles and cycles[0]["status"] in {"CREATED","RUNNING"} else (cycles[0]["cycle_id"] if cycles else None)
            cycle_budget=int(cycles[0]["token_budget"]) if cycles else self.runtime.rnd_trigger.token_budget
            current_day=self.runtime.current_day or 0
            stage_start=(current_day//max(1,self.rnd_trigger.cycle_days))*max(1,self.rnd_trigger.cycle_days)
            return self.ledger.snapshot(current_day=current_day,rnd_budget=cycle_budget,current_cycle_id=cycle_id,runtime_stage_start_day=stage_start)
        if command=="GET_LLM_TELEMETRY":return {"requests":self.store.recent_llm_requests(limit=min(int(args.get("limit",100)),1000))}
        if command=="GET_RND":return {"readiness":self.runtime.rnd_service.readiness() if self.runtime.rnd_service else {"mode":"DISABLED"},"cycles":self.rnd_trigger.list_cycles(limit=100),"candidate_skills":self.skills.list(status="CANDIDATE",limit=500)}
        if command=="MARK_RND_HANDLED":
            cycle_id=str(args.get("cycle_id") or "")
            if not cycle_id:raise ValueError("缺少研发周期")
            with self.store.connection() as conn:
                cursor=conn.execute("UPDATE rnd_cycles SET handled=1,updated_at=CURRENT_TIMESTAMP WHERE cycle_id=?",(cycle_id,))
            if cursor.rowcount!=1:raise ValueError("没有找到这次研发记录")
            return {"handled":True,"cycle_id":cycle_id}
        if command=="RESEARCH_MODS":
            queries=[str(x) for x in args.get("queries",[]) if str(x).strip()];dest=self.settings.data_dir/"handoff"/f"manual-mod-research-{int(time.time())}"
            results=await self.mod_research.research_to_handoff(queries,dest);return {"handoff_dir":str(dest),"results":results,"installation":"HANDOFF_ONLY"}
        if command=="LIST_PLAYERS":
            envelope=await self.gateway.request_message(MessageType.LIST_PLAYERS,{},timeout=10)
            return {"players":list(envelope.payload.get("players") or []),"selected_owner_uuid":self.settings.owner_uuid or ""}
        if command=="SELECT_OWNER":
            selected=str(args.get("uuid") or "")
            envelope=await self.gateway.request_message(MessageType.LIST_PLAYERS,{},timeout=10)
            players=list(envelope.payload.get("players") or [])
            match=next((row for row in players if str(row.get("uuid") or "")==selected),None)
            if match is None:raise ValueError("所选玩家当前不在线，请重新扫描")
            self.settings.owner_uuid=selected
            return {"selected_owner_uuid":selected,"selected_owner_name":str(match.get("name") or "")}
        if command=="DISCOVER_MAIDS":
            if not self.settings.owner_uuid:raise ValueError("请先从当前在线玩家中选择主人")
            envelope=await self.gateway.request_message(MessageType.DISCOVER_MAIDS,{"owner_uuid":self.settings.owner_uuid or ""},timeout=10);maids=list(envelope.payload.get("maids") or [])
            if self.settings.owner_uuid:maids=[m for m in maids if str(m.get("owner_uuid") or "")==self.settings.owner_uuid]
            return {"maids":maids}
        if command=="BIND_MAID":
            if not self.settings.owner_uuid:raise ValueError("未选择主人，禁止绑定任意女仆")
            maid_uuid=str(args["maid_uuid"]);envelope=await self.gateway.request_message(MessageType.BIND_MAID,{"maid_uuid":maid_uuid,"owner_uuid":self.settings.owner_uuid or ""},timeout=10)
            if bool(envelope.payload.get("ok")):self.gateway.bound_maid_uuid=maid_uuid
            return envelope.payload
        if command=="UNBIND_MAID":
            envelope=await self.gateway.request_message(MessageType.UNBIND_MAID,{"maid_uuid":self.gateway.bound_maid_uuid or ""},timeout=10);self.gateway.bound_maid_uuid=None;return envelope.payload
        if command=="GET_DIAGNOSTICS":return self._diagnostics()
        if command=="EXPORT_DIAGNOSTICS":return {"path":str(self._export_diagnostics())}
        if command=="GET_RECENT_UI_EVENTS":return {"events":self.event_bus.recent(int(args.get("limit",100)))}
        raise ValueError(f"未知控制命令：{command}")

    def _status(self)->dict[str,Any]:
        return self.runtime.status_snapshot()|{"control_identity":{"product":"MaidAI-Agent","protocol_version":1,"instance_id":self._instance_id},"start_gate":self._start_gate()}

    @staticmethod
    def _profile_signature(profile:Any)->str:
        if profile is None:return ""
        raw="|".join((str(profile.base_url).rstrip("/"),str(profile.chat_completions_path),str(profile.model)))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _probe_ok(self,kind:str,profile:Any)->bool:
        probe=dict(self.settings.api_probes.get(kind) or {})
        try:age=int(time.time()*1000)-int(probe.get("last_probe_at") or 0)
        except (TypeError,ValueError):return False
        return bool(profile and probe.get("last_probe_ok") is True and 0<=age<=24*60*60*1000 and probe.get("profile_signature")==self._profile_signature(profile))

    def _start_gate(self)->dict[str,Any]:
        missing=[];hello=dict(self.gateway.hello or {});snapshot=self.gateway.latest_snapshot
        if not self.settings.setup_complete:missing.append("首次设置尚未完成")
        if not self.gateway.connected:missing.append("Minecraft Bridge 未连接")
        expected={"minecraft":"1.20.1","forge":"47.x","tlm":"1.5.3","bridge_version":"0.3.0","protocol_version":1}
        versions_ok=bool(hello) and str(hello.get("minecraft"))==expected["minecraft"] and forge_47_supported(hello.get("forge")) and str(hello.get("bridge_version"))==expected["bridge_version"] and int(hello.get("protocol_version") or 0)==1 and str(hello.get("tlm") or "").startswith(expected["tlm"])
        if self.gateway.connected and not versions_ok:missing.append("Minecraft / Forge / TLM / Bridge 版本不匹配")
        if not self.settings.owner_uuid:missing.append("尚未选择当前在线玩家")
        if not self.gateway.bound_maid_uuid or snapshot is None:missing.append("尚未绑定并确认 EntityMaid")
        elif self.settings.owner_uuid and str(snapshot.owner_uuid or "")!=str(self.settings.owner_uuid):missing.append("女仆主人与所选玩家不一致")
        if self.settings.runtime_profile is None or self.runtime.provider is None:missing.append("Runtime API 未配置或凭据不可用")
        elif not self._probe_ok("runtime",self.settings.runtime_profile):missing.append("Runtime API 最近没有通过真实测试")
        readiness=self.runtime.rnd_service.readiness() if self.runtime.rnd_service else {"mode":"DISABLED","missing":["rnd_service"]}
        rnd_provider=bool(self.runtime.rnd_service and self.runtime.rnd_service.orchestrator and self.runtime.rnd_service.orchestrator.provider)
        if self.settings.rnd_profile is None or not rnd_provider:missing.append("R&D API 未配置或凭据不可用")
        elif not self._probe_ok("rnd",self.settings.rnd_profile):missing.append("R&D API 最近没有通过真实测试")
        if str(getattr(readiness.get("mode"),"value",readiness.get("mode")))!="FULL_HARNESS":missing.append("R&D Harness 或 Source Workspace 未就绪")
        if self.settings.minecraft_restart_required and not self.gateway.connected:missing.append("Bridge 端口已变化，需要重启 Minecraft")
        return {"ready":not missing,"missing":missing,"versions_ok":versions_ok,"expected_versions":expected,"actual_versions":hello,"rnd_readiness":readiness}

    def _diagnostics(self)->dict[str,Any]:
        return {"product_version":"0.3.0","python":sys.version,"platform":platform.platform(),"pid":os.getpid(),"data_dir":str(self.settings.data_dir),
            "database_exists":self.settings.database_path.exists(),"bridge":{"connected":self.gateway.connected,"hello":self.gateway.hello,"last_message_age_seconds":time.monotonic()-self.gateway.last_message_monotonic if self.gateway.last_message_monotonic else None,"bound_maid_uuid":self.gateway.bound_maid_uuid},
            "runtime":self.runtime.status_snapshot(),"rnd":self.runtime.rnd_service.readiness() if self.runtime.rnd_service else {"mode":"DISABLED"},"recent_llm_requests":self.store.recent_llm_requests(20)}

    def _export_diagnostics(self)->Path:
        root=self.settings.data_dir/"diagnostics";root.mkdir(parents=True,exist_ok=True);target=root/f"MaidAI诊断-{int(time.time())}.zip";temp=root/f"bundle-{uuid4()}";temp.mkdir()
        try:
            (temp/"diagnostics.json").write_text(json.dumps(self._diagnostics(),ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            config={"host":self.settings.host,"bridge_port":self.settings.bridge_port,"control_port":self.settings.control_port,"runtime_profile":self.settings.runtime_profile.model_dump(mode="json",exclude={"extra_headers"}) if self.settings.runtime_profile else None,"rnd_profile":self.settings.rnd_profile.model_dump(mode="json",exclude={"extra_headers"}) if self.settings.rnd_profile else None,"strict_survival":self.settings.strict_survival}
            (temp/"config_sanitized.json").write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
            logs=self.settings.data_dir/"logs"
            if logs.exists():shutil.copytree(logs,temp/"logs",dirs_exist_ok=True)
            with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as archive:
                for path in temp.rglob("*"):
                    if path.is_file():archive.write(path,path.relative_to(temp))
        finally:shutil.rmtree(temp,ignore_errors=True)
        return target
