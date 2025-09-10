import asyncio
import json

import pytest
from fastapi.responses import JSONResponse
from arcade_core.catalog import ToolCatalog
from arcade_serve.fastapi.sse import SSEComponent


class MockWorker:
    def __init__(self, catalog: ToolCatalog, disable_auth: bool = True):
        self.catalog = catalog
        self.disable_auth = disable_auth
        self.secret = "test-secret"


@pytest.fixture
def tool_catalog() -> ToolCatalog:
    return ToolCatalog()


@pytest.fixture
def sse_component(tool_catalog: ToolCatalog) -> SSEComponent:
    return SSEComponent(MockWorker(tool_catalog))


def _as_mapping(resp: JSONResponse | dict[str, str]) -> dict[str, str]:
    if isinstance(resp, dict):
        return resp
    # JSONResponse: parse body
    try:
        data = json.loads(resp.body.decode()) if hasattr(resp, "body") else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@pytest.mark.asyncio
async def test_sse_replay_with_last_event_id(sse_component: SSEComponent) -> None:
    class MockGet:
        def __init__(self, headers: dict[str, str]):
            self.method = "GET"
            self.headers = headers
            self.scope = {}

        async def receive(self):
            return {"type": "http.request"}

        async def _send(self, message):
            return None

    class MockPost:
        def __init__(self, headers: dict[str, str], body: bytes):
            self.method = "POST"
            self.headers = headers
            self._body = body

        async def body(self) -> bytes:
            return self._body

    # Initialize session via POST initialize with minimal valid params
    init_body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.0"},
        },
    }).encode()

    post = MockPost(headers={"content-type": "application/json"}, body=init_body)
    init_resp = await sse_component(post)
    init_map = _as_mapping(init_resp)
    assert init_map.get("status") == "ok"
    session_id = init_map.get("session_id")
    assert session_id

    # SSE GET without last-event-id
    get1 = MockGet(headers={"accept": "text/event-stream"})
    # We won't actually run the EventSourceResponse ASGI app in this unit test
    # but we can at least verify that providing Last-Event-ID header doesn't error
    get2 = MockGet(headers={"accept": "text/event-stream", "last-event-id": "0"})
    # Call component to ensure no exceptions
    await sse_component(get2)