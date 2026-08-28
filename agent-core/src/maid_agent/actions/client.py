from __future__ import annotations

from typing import Any
from uuid import uuid4

from maid_agent.actions.catalog import BRIDGE_TOOLS, CATALOG, SAFE_TOOLS, ToolValidationError
from maid_agent.protocol.models import ActionRequest, ActionResult
from maid_agent.transport.ws_server import BridgeGateway


class ActionClient:
    def __init__(self, gateway: BridgeGateway):
        self.gateway = gateway

    async def execute(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        timeout_ticks: int = 1200,
        request_id: str | None = None,
    ) -> ActionResult:
        if tool not in BRIDGE_TOOLS:
            if tool in SAFE_TOOLS:
                raise ToolValidationError("RUNTIME_TOOL", f"{tool} 必须由 Runtime 执行")
            raise ToolValidationError("UNKNOWN_TOOL", f"工具未注册：{tool}")
        normalized = CATALOG.validate(tool, args)
        request = ActionRequest(
            request_id=request_id or str(uuid4()),
            action=tool,
            args=normalized,
            timeout_ticks=timeout_ticks,
        )
        return await self.gateway.request_action(request)

    async def stop(self) -> ActionResult:
        return await self.execute("stop", {}, timeout_ticks=100)
