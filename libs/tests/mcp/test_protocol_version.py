import asyncio
import json

import pytest
from arcade_core.catalog import ToolCatalog
from arcade_serve.fastapi.sse import SSEComponent
from arcade_serve.mcp.types import LATEST_PROTOCOL_VERSION


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


@pytest.mark.asyncio
async def test_sse_protocol_version_reject_post(sse_component: SSEComponent) -> None:
    class MockRequest:
        def __init__(self, headers: dict[str, str], body: bytes):
            self.method = "POST"
            self.headers = headers
            self._body = body

        async def body(self) -> bytes:
            return self._body

    req = MockRequest(
        headers={
            "content-type": "application/json",
            "mcp-protocol-version": "bad-version",
        },
        body=b'{"method":"initialize","id":1}',
    )
    resp = await sse_component(req)
    assert resp["status"] == "error"
    assert "Unsupported protocol version" in resp["message"]