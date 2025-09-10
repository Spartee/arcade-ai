import asyncio
import json
import uuid
from typing import Any

from arcade_serve.mcp.types import JSONRPC_VERSION


class RequestManager:
    """
    Manages server-initiated JSON-RPC requests to the client and correlates responses.
    """

    def __init__(self, write_stream: Any) -> None:
        self._write_stream = write_stream
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def send_request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = 60.0) -> Any:
        request_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[request_id] = fut

        # Serialize to a single NDJSON line
        line = json.dumps(payload)
        if not line.endswith("\n"):
            line += "\n"
        await self._write_stream.send(line)

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

    async def resolve_response(self, response: dict[str, Any]) -> None:
        """Resolve a pending request future from a JSON-RPC response or error dict."""
        resp_id = str(response.get("id")) if response.get("id") is not None else None
        if not resp_id:
            return
        fut: asyncio.Future | None = None
        async with self._lock:
            fut = self._pending.get(resp_id)
        if fut and not fut.done():
            if "error" in response and response["error"] is not None:
                fut.set_exception(RuntimeError(str(response["error"])) )
            else:
                fut.set_result(response.get("result"))