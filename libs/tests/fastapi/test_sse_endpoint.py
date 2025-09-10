import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcade_serve.fastapi.sse import SSEComponent
from arcade_serve.fastapi.worker import FastAPIWorker


def test_sse_post_initialize_and_followup() -> None:
    app = FastAPI()
    worker = FastAPIWorker(app=app, disable_auth=True)
    worker.register_component(SSEComponent)

    client = TestClient(app)

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.0"},
        },
    }

    init_resp = client.post("/mcp", json=init_payload, headers={"content-type": "application/json"})
    assert init_resp.status_code == 200
    data = init_resp.json()
    assert data.get("status") == "ok"
    session_id = data.get("session_id")
    assert isinstance(session_id, str) and len(session_id) > 0

    # Follow up with ping request using session header
    ping_payload = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    ping_resp = client.post(
        "/mcp",
        json=ping_payload,
        headers={"content-type": "application/json", "mcp-session-id": session_id},
    )
    assert ping_resp.status_code == 200
    ping_data = ping_resp.json()
    # ping returns JSON-RPC result stream; verify it contains jsonrpc
    assert ping_data.get("jsonrpc") == "2.0" or ping_data.get("status") == "ok"