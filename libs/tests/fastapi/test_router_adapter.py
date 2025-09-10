import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcade_serve.core.common import RouteSpec
from arcade_serve.fastapi.worker import FastAPIWorker


def test_add_raw_route_mounts_spec() -> None:
    app = FastAPI()
    worker = FastAPIWorker(app=app, disable_auth=True)

    def endpoint() -> dict[str, Any]:
        return {"ok": True}

    spec = RouteSpec(
        path="/test",
        methods=["GET"],
        endpoint=endpoint,
        operation_id="test_endpoint",
        description="Test endpoint",
        summary="Test endpoint",
        tags=["Test"],
    )

    worker.router.add_raw_route(spec)

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}