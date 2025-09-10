import json
from typing import Any, Callable, Coroutine, cast

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from arcade_serve.core.base import BaseWorker
from arcade_serve.core.common import RouteSpec, Router, WorkerComponent
from arcade_serve.fastapi.auth import validate_engine_request
from arcade_serve.mcp.server import MCPServer
from arcade_serve.mcp.types import LATEST_PROTOCOL_VERSION


class StreamableHTTPComponent(WorkerComponent):
    def __init__(
        self,
        worker: BaseWorker,
        local_context: dict[str, Any] | None = None,
        *,
        log_level: str = "INFO",
        rate_limit_per_min: int = 60,
        debounce_ms: int = 100,
        server_name: str = "ArcadeMCP",
        server_version: str = "0.1.0",
        server_title: str | None = "ArcadeMCP",
    ) -> None:
        super().__init__(worker)
        self.mcp_server = MCPServer(
            worker.catalog,
            auth_disabled=worker.disable_auth,
            local_context=local_context,
            log_level=log_level,
            rate_limit_per_min=rate_limit_per_min,
            debounce_ms=debounce_ms,
            server_name=server_name,
            server_version=server_version,
            server_title=server_title,
        )
        self.security = HTTPBearer(auto_error=not worker.disable_auth)

    def register(self, router: Router) -> None:
        auth_dependency = self.get_auth_dependency()
        worker_typed = cast(BaseWorker, self.worker)
        spec = RouteSpec(
            path="/mcp",
            methods=["POST"],
            endpoint=self.__call__,
            response_class=JSONResponse,
            dependencies=[Depends(auth_dependency)] if not worker_typed.disable_auth else [],
            operation_id="mcp_streamable_http",
            description="MCP Streamable HTTP (JSON per request)",
            summary="MCP Streamable HTTP",
            tags=["MCP"],
        )
        router.add_raw_route(spec)

    def get_auth_dependency(self) -> Callable[[], Coroutine[Any, Any, None]]:
        worker_typed = cast(BaseWorker, self.worker)

        async def dependency(
            credentials: HTTPAuthorizationCredentials | None = Depends(self.security),
        ) -> None:
            if not worker_typed.disable_auth and credentials:
                await validate_engine_request(worker_typed.secret, credentials)

        return dependency

    async def __call__(self, request: Request) -> JSONResponse:  # type: ignore[override]
        # Protocol version validation (optional; accept if absent)
        protocol_version = request.headers.get("mcp-protocol-version")
        if protocol_version is not None and protocol_version != LATEST_PROTOCOL_VERSION:
            return JSONResponse(
                content={
                    "error": f"Unsupported protocol version: {protocol_version}",
                    "supported": [LATEST_PROTOCOL_VERSION],
                    "status": "error",
                },
                status_code=400,
            )

        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/json"):
            return JSONResponse(
                content={"error": "Content-Type must be application/json", "status": "error"},
                status_code=400,
            )

        try:
            body_bytes = await request.body()
            payload = json.loads(body_bytes) if body_bytes else {}
        except Exception as e:
            return JSONResponse(
                content={"error": f"Invalid JSON: {e!s}", "status": "error"}, status_code=400
            )

        try:
            response = await self.mcp_server.handle_message(payload)
            if response is None:
                # Notifications / no response
                return JSONResponse(content=None, status_code=202)

            if hasattr(response, "model_dump"):
                return JSONResponse(content=response.model_dump(exclude_none=True))
            if isinstance(response, dict):
                return JSONResponse(content=response)
            # Fallback
            return JSONResponse(content={"result": str(response)})
        except Exception as e:
            return JSONResponse(
                content={"error": f"Processing error: {e!s}", "status": "error"},
                status_code=500,
            )