from __future__ import annotations

import asyncio,logging,time
from collections import OrderedDict
from contextlib import suppress
from typing import Any
from websockets.asyncio.server import ServerConnection,serve
from websockets.exceptions import ConnectionClosed

from maid_agent.control.events import EventBus
from maid_agent.protocol.models import ActionRequest,ActionResult,BridgeEvent,MessageType,ProtocolEnvelope,StateSnapshot

log=logging.getLogger(__name__)


class BridgeDisconnected(RuntimeError):pass


class BridgeGateway:
    def __init__(self,host:str,port:int,event_bus:EventBus):
        self.host=host;self.port=port;self.event_bus=event_bus;self._server=None;self._ws:ServerConnection|None=None;self._session_id=""
        self._send_lock=asyncio.Lock();self._snapshot_condition=asyncio.Condition();self._snapshot_version=0;self.latest_snapshot:StateSnapshot|None=None
        self.hello:dict[str,Any]|None=None;self.last_message_monotonic=0.0;self.bound_maid_uuid:str|None=None
        self._result_waiters:dict[str,asyncio.Future[ActionResult]]={};self._generic_waiters:dict[str,asyncio.Future[ProtocolEnvelope]]={}
        self._action_cache:OrderedDict[str,ActionResult]=OrderedDict();self._event_queue:asyncio.Queue[BridgeEvent]=asyncio.Queue(maxsize=1000);self._seen_events:OrderedDict[str,None]=OrderedDict()
        self._hello_snapshot_version=0;self._last_snapshot_session_id="";self._last_snapshot_maid_uuid:str|None=None;self._connection_generation=0

    @property
    def connected(self)->bool:return self._ws is not None
    @property
    def session_id(self)->str:return self._session_id
    @property
    def snapshot_version(self)->int:return self._snapshot_version
    @property
    def resync_ready(self)->bool:
        return bool(
            self.hello is not None
            and self.latest_snapshot is not None
            and self._snapshot_version>self._hello_snapshot_version
            and self._last_snapshot_session_id==self._session_id
            and (self.bound_maid_uuid is None or self._last_snapshot_maid_uuid==self.bound_maid_uuid)
        )

    def cached_results(self)->dict[str,ActionResult]:return dict(self._action_cache)

    async def start(self)->None:
        self._server=await serve(self._handler,self.host,self.port,ping_interval=None,max_size=8*1024*1024)
        log.info("Bridge WebSocket listening on %s:%s",self.host,self.port)

    async def close(self)->None:
        if self._ws:await self._ws.close(code=1001,reason="agent shutdown")
        if self._server:self._server.close();await self._server.wait_closed()
        self._fail_waiters(BridgeDisconnected("bridge closed"))

    async def _handler(self,ws:ServerConnection)->None:
        if self._ws is not None:await ws.close(code=1013,reason="a bridge is already connected");return
        self._ws=ws;self._connection_generation+=1;self.latest_snapshot=None;self.hello=None;self._session_id="";self._last_snapshot_session_id="";self._last_snapshot_maid_uuid=None
        self.last_message_monotonic=time.monotonic();self.event_bus.publish("BRIDGE_STATUS",{"connected":True,"generation":self._connection_generation})
        try:
            async for raw in ws:
                self.last_message_monotonic=time.monotonic()
                try:envelope=ProtocolEnvelope.model_validate_json(raw)
                except Exception as exc:log.warning("invalid bridge message: %s",exc);continue
                await self._on_message(envelope)
        except ConnectionClosed:pass
        finally:
            if self._ws is ws:self._ws=None
            self.hello=None;self.latest_snapshot=None;self._session_id="";self._last_snapshot_session_id="";self._last_snapshot_maid_uuid=None;self._fail_waiters(BridgeDisconnected("bridge disconnected"));self.event_bus.publish("BRIDGE_STATUS",{"connected":False})

    async def _on_message(self,envelope:ProtocolEnvelope)->None:
        kind=str(getattr(envelope.type,"value",envelope.type))
        if kind==MessageType.HELLO:
            self.hello=envelope.payload;self._session_id=envelope.session_id;self._hello_snapshot_version=self._snapshot_version;self.event_bus.publish("BRIDGE_HELLO",envelope.payload)
            await self.send(ProtocolEnvelope.make(MessageType.STATE_RESYNC,{"reason":"hello"},session_id=self._session_id,maid_uuid=self.bound_maid_uuid))
        elif kind==MessageType.PING:await self.send(ProtocolEnvelope.make(MessageType.PONG,{"echo":envelope.message_id},session_id=self._session_id))
        elif kind in {MessageType.STATE_SNAPSHOT,MessageType.STATE_RESYNC} and "dimension" in envelope.payload:
            if self.hello is None or envelope.session_id!=self._session_id:
                log.warning("ignoring state from a session that has not completed HELLO")
                return
            incoming_maid=envelope.maid_uuid
            if self.bound_maid_uuid and incoming_maid and incoming_maid!=self.bound_maid_uuid:
                self.event_bus.publish("BRIDGE_MAID_MISMATCH",{"expected":self.bound_maid_uuid,"received":incoming_maid})
                return
            if self.bound_maid_uuid is None and incoming_maid:self.bound_maid_uuid=incoming_maid
            payload=dict(envelope.payload);payload.setdefault("game_tick",envelope.game_tick);self.latest_snapshot=StateSnapshot.model_validate(payload)
            self._last_snapshot_session_id=envelope.session_id;self._last_snapshot_maid_uuid=incoming_maid
            async with self._snapshot_condition:self._snapshot_version+=1;self._snapshot_condition.notify_all()
            self.event_bus.publish("STATE_SNAPSHOT",self.latest_snapshot.model_dump(mode="json"))
        elif kind==MessageType.EVENT:
            payload=dict(envelope.payload);payload.setdefault("game_tick",envelope.game_tick);payload.setdefault("maid_uuid",envelope.maid_uuid)
            if "event_id" not in payload:payload["event_id"]=envelope.message_id
            if "event_type" not in payload:payload["event_type"]=str(payload.pop("type","UNKNOWN"))
            event=BridgeEvent.model_validate(payload)
            if event.event_id not in self._seen_events:
                self._seen_events[event.event_id]=None
                while len(self._seen_events)>5000:self._seen_events.popitem(last=False)
                if self._event_queue.full():
                    with suppress(asyncio.QueueEmpty):
                        self._event_queue.get_nowait()
                self._event_queue.put_nowait(event)
                self.event_bus.publish("BRIDGE_EVENT",event.model_dump(mode="json"))
        elif kind==MessageType.ACTION_RESULT:
            result=ActionResult.model_validate(envelope.payload);self._action_cache[result.request_id]=result
            while len(self._action_cache)>512:self._action_cache.popitem(last=False)
            fut=self._result_waiters.pop(result.request_id,None)
            if fut and not fut.done():fut.set_result(result)
            self.event_bus.publish("ACTION_RESULT",result.model_dump(mode="json"))
        elif kind in {MessageType.MAID_LIST,MessageType.PLAYER_LIST,MessageType.CONTROL_RESULT}:
            request_id=str(envelope.payload.get("request_id") or envelope.message_id);fut=self._generic_waiters.pop(request_id,None)
            if fut and not fut.done():fut.set_result(envelope)
            self.event_bus.publish(str(kind),envelope.payload)
        elif kind==MessageType.ACTION_ACK:self.event_bus.publish("ACTION_ACK",envelope.payload)

    async def send(self,envelope:ProtocolEnvelope)->None:
        ws=self._ws
        if ws is None:raise BridgeDisconnected("bridge is not connected")
        async with self._send_lock:await ws.send(envelope.model_dump_json())

    async def request_action(self,request:ActionRequest,*,timeout_seconds:float|None=None)->ActionResult:
        if request.request_id in self._action_cache:return self._action_cache[request.request_id]
        if request.request_id in self._result_waiters:return await asyncio.shield(self._result_waiters[request.request_id])
        fut=asyncio.get_running_loop().create_future();self._result_waiters[request.request_id]=fut
        envelope=ProtocolEnvelope.make(MessageType.ACTION_REQUEST,{"request_id":request.request_id,"action":request.action,"args":request.args,"timeout_ticks":request.timeout_ticks},session_id=self._session_id,maid_uuid=self.bound_maid_uuid)
        try:
            await self.send(envelope);timeout=timeout_seconds or max(10.0,request.timeout_ticks/20+8)
            return await asyncio.wait_for(asyncio.shield(fut),timeout=timeout)
        except Exception:
            self._result_waiters.pop(request.request_id,None);raise

    async def request_message(self,message_type:MessageType|str,payload:dict[str,Any],*,timeout:float=10)->ProtocolEnvelope:
        request_id=str(payload.get("request_id") or ProtocolEnvelope.make("x").message_id);payload=dict(payload);payload["request_id"]=request_id
        fut=asyncio.get_running_loop().create_future();self._generic_waiters[request_id]=fut
        try:
            await self.send(ProtocolEnvelope.make(message_type,payload,session_id=self._session_id,maid_uuid=self.bound_maid_uuid));return await asyncio.wait_for(fut,timeout)
        except Exception:self._generic_waiters.pop(request_id,None);raise

    async def wait_for_snapshot(self,after_version:int=0,*,timeout:float|None=None)->tuple[int,StateSnapshot]:
        async def wait():
            async with self._snapshot_condition:
                await self._snapshot_condition.wait_for(lambda:self._snapshot_version>after_version and self.latest_snapshot is not None)
                assert self.latest_snapshot is not None;return self._snapshot_version,self.latest_snapshot
        return await asyncio.wait_for(wait(),timeout) if timeout else await wait()

    async def next_event(self,*,timeout:float|None=None)->BridgeEvent:
        return await asyncio.wait_for(self._event_queue.get(),timeout) if timeout else await self._event_queue.get()

    async def safe_idle(self,reason:str)->None:
        if self.connected:
            with suppress(Exception):await self.send(ProtocolEnvelope.make(MessageType.SAFE_IDLE,{"reason":reason},session_id=self._session_id,maid_uuid=self.bound_maid_uuid))

    def _fail_waiters(self,exc:Exception)->None:
        for mapping in (self._result_waiters,self._generic_waiters):
            for fut in mapping.values():
                if not fut.done():fut.set_exception(exc)
            mapping.clear()
