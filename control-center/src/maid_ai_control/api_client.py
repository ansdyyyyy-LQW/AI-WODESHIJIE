from __future__ import annotations

import json
from uuid import uuid4
from PySide6.QtCore import QObject,QTimer,Signal,Slot,QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket


class ApiClient(QObject):
    connectedChanged=Signal(bool);result=Signal(str,dict);failed=Signal(str,str,str);event=Signal(str,dict);rawMessage=Signal(dict)
    def __init__(self,url:str,token:str,parent:QObject|None=None):
        super().__init__(parent);self.url=url;self.token=token;self.socket=QWebSocket();self.pending:dict[str,str]={};self._connected=False;self._closed=False
        self.socket.connected.connect(self._on_connected);self.socket.disconnected.connect(self._on_disconnected);self.socket.textMessageReceived.connect(self._on_message);self.socket.errorOccurred.connect(self._on_error)
        self.reconnect=QTimer(self);self.reconnect.setInterval(2000);self.reconnect.timeout.connect(self.connect)
    @property
    def connected(self)->bool:return self._connected
    @Slot()
    def connect(self)->None:
        if self._closed:return
        if self.socket.state() in {QAbstractSocket.ConnectedState,QAbstractSocket.ConnectingState}:return
        self.socket.open(QUrl(self.url))
    def update_endpoint(self,url:str,token:str)->None:self.url=url;self.token=token;self._closed=False;self.socket.abort();self.connect()
    def close(self)->None:self._closed=True;self.reconnect.stop();self.socket.close()
    def command(self,command:str,args:dict|None=None)->str:
        request_id=str(uuid4());payload={"request_id":request_id,"command":command.upper(),"args":args or {},"token":self.token}
        if self._connected:self.pending[request_id]=command.upper();self.socket.sendTextMessage(json.dumps(payload,ensure_ascii=False))
        else:self.failed.emit(command,"NOT_CONNECTED","Agent Core 尚未连接")
        return request_id
    def _on_connected(self)->None:self._connected=True;self.reconnect.stop();self.connectedChanged.emit(True)
    def _on_disconnected(self)->None:self._connected=False;self.connectedChanged.emit(False);self.reconnect.start() if not self._closed else None
    def _on_error(self,error)->None:
        if not self._closed and not self.reconnect.isActive():self.reconnect.start()
    def _on_message(self,text:str)->None:
        try:data=json.loads(text)
        except json.JSONDecodeError:return
        self.rawMessage.emit(data)
        if data.get("type")=="EVENT":self.event.emit(str(data.get("event","EVENT")),dict(data.get("payload") or {}));return
        request_id=str(data.get("request_id") or "");command=self.pending.pop(request_id,"UNKNOWN")
        if data.get("ok"):self.result.emit(command,dict(data.get("data") or {}))
        else:self.failed.emit(command,str(data.get("code") or "ERROR"),str(data.get("message") or "请求失败"))
