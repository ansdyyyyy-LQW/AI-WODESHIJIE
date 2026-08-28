from __future__ import annotations

import pytest
pytest.importorskip("PySide6")
from pathlib import Path
import socket
import threading
from maid_ai_control.config import ConfigManager
from maid_ai_control.process_supervisor import ProcessSupervisor


def test_bridge_config_is_synchronized_to_agent_port(tmp_path:Path):
    cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data")
    game=tmp_path/"minecraft";game.mkdir()
    cfg.data["minecraft_dir"]=str(game);cfg.data["bridge_port"]=19001;cfg.save()
    supervisor=ProcessSupervisor(cfg)
    supervisor.sync_bridge_config()
    text=(game/"config"/"maid_ai_bridge-common.toml").read_text(encoding="utf-8")
    assert 'websocket_url = "ws://127.0.0.1:19001"' in text


def test_unrelated_listener_is_not_treated_as_external_agent(tmp_path:Path):
    listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen(4);occupied=listener.getsockname()[1]
    stop=threading.Event()
    def serve():
        while not stop.is_set():
            try:
                listener.settimeout(.2);conn,_=listener.accept();conn.close()
            except (TimeoutError,OSError):
                continue
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    try:
        cfg=ConfigManager(tmp_path/"agent.json",data_dir=tmp_path/"data");cfg.data["control_port"]=occupied;cfg.data["bridge_port"]=occupied+1;cfg.save()
        supervisor=ProcessSupervisor(cfg)
        assert supervisor.control_identity() is None
        supervisor._resolve_ports()
        assert cfg.data["control_port"]!=occupied
    finally:
        stop.set();listener.close();thread.join(timeout=1)
