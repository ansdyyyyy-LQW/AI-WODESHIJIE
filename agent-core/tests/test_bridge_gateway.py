from __future__ import annotations

import asyncio
import socket

import pytest
from websockets.asyncio.client import connect

from maid_agent.control.events import EventBus
from maid_agent.protocol.models import ActionRequest, ActionStatus, MessageType, ProtocolEnvelope
from maid_agent.transport.ws_server import BridgeGateway


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_bridge_hello_snapshot_action_and_discovery() -> None:
    port = free_port()
    gateway = BridgeGateway("127.0.0.1", port, EventBus())
    await gateway.start()

    async def fake_bridge() -> None:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(ProtocolEnvelope.make(
                MessageType.HELLO,
                {"bridge_version": "0.1.0", "tlm": "1.5.3"},
                session_id="test-session",
            ).model_dump_json())
            resync = ProtocolEnvelope.model_validate_json(await ws.recv())
            assert resync.type == MessageType.STATE_RESYNC
            await ws.send(ProtocolEnvelope.make(
                MessageType.STATE_SNAPSHOT,
                {
                    "dimension": "minecraft:overworld",
                    "day": 5,
                    "time_of_day": 100,
                    "position": {"x": 0, "y": 64, "z": 0},
                    "health": 20,
                    "max_health": 20,
                    "inventory": [],
                },
                session_id="test-session",
                game_tick=99,
            ).model_dump_json())

            action_env = ProtocolEnvelope.model_validate_json(await ws.recv())
            assert action_env.type == MessageType.ACTION_REQUEST
            request_id = action_env.payload["request_id"]
            await ws.send(ProtocolEnvelope.make(
                MessageType.ACTION_ACK,
                {"request_id": request_id, "action_id": "action-1"},
                session_id="test-session",
            ).model_dump_json())
            await ws.send(ProtocolEnvelope.make(
                MessageType.ACTION_RESULT,
                {
                    "request_id": request_id,
                    "action_id": "action-1",
                    "status": "SUCCESS",
                    "code": "ARRIVED",
                    "data": {"distance": 0.1},
                    "world_delta": {},
                },
                session_id="test-session",
            ).model_dump_json())

            discover = ProtocolEnvelope.model_validate_json(await ws.recv())
            assert discover.type == MessageType.DISCOVER_MAIDS
            await ws.send(ProtocolEnvelope.make(
                MessageType.MAID_LIST,
                {"request_id": discover.payload["request_id"], "maids": [{"uuid": "maid-1", "name": "Maid"}]},
                session_id="test-session",
            ).model_dump_json())
            await asyncio.sleep(0.05)

    bridge_task = asyncio.create_task(fake_bridge())
    version, snapshot = await gateway.wait_for_snapshot(timeout=2)
    assert version == 1
    assert snapshot.game_tick == 99
    result = await gateway.request_action(ActionRequest(action="move_to", args={"x": 1, "y": 64, "z": 1}), timeout_seconds=2)
    assert result.status == ActionStatus.SUCCESS
    assert result.code == "ARRIVED"
    response = await gateway.request_message(MessageType.DISCOVER_MAIDS, {}, timeout=2)
    assert response.payload["maids"][0]["uuid"] == "maid-1"
    await bridge_task
    await gateway.close()
