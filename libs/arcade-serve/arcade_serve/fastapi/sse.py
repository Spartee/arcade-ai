import asyncio
from collections.abc import Coroutine
from typing import Any, Callable

if not hasattr(asyncio, "coroutine"):

    def _asyncio_coroutine_shim(
        func: Callable[..., Any],
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return _wrapper

    asyncio.coroutine = _asyncio_coroutine_shim  # type: ignore[assignment]
import contextlib
import json
import time
import uuid
from logging import getLogger

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse

from arcade_serve.core.base import BaseWorker
from arcade_serve.core.common import (
    Router,
    TransportSession,
    WorkerComponent,
)
from arcade_serve.fastapi.auth import validate_engine_request
from arcade_serve.fastapi.event_store import EventStore, InMemoryEventStore
from arcade_serve.mcp.server import MCPServer
from arcade_serve.mcp.types import LATEST_PROTOCOL_VERSION

SESSION_TIMEOUT_SECONDS = 300  # 5 minutes
CLEANUP_INTERVAL_SECONDS = 10  # Check every 10 seconds instead of 60
MAX_SESSIONS = 1000  # Maximum number of concurrent sessions

logger = getLogger("arcade.mcp")


class SSEComponent(WorkerComponent):
    def __init__(
        self,
        worker: BaseWorker,
        local_context: dict[str, Any] | None = None,
        *,
        session_timeout_sec: int = 300,
        cleanup_interval_sec: int = 10,
        max_sessions: int = 1000,
        max_queue: int = 1000,
        log_level: str = "INFO",
        rate_limit_per_min: int = 60,
        debounce_ms: int = 100,
        server_name: str = "ArcadeMCP",
        server_version: str = "0.1.0",
        server_title: str | None = "ArcadeMCP",
        event_store: EventStore | None = None,
    ) -> None:
        super().__init__(worker)
        self._worker_base: BaseWorker = worker
        self.sessions: dict[str, TransportSession] = {}
        self.sessions_lock = asyncio.Lock()  # Add lock for thread-safe operations
        self.session_timeout_sec = session_timeout_sec
        self.cleanup_interval_sec = cleanup_interval_sec
        self.max_sessions = max_sessions
        self.max_queue = max_queue
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
        self._cleanup_task: asyncio.Task | None = None
        # Initialize HTTPBearer with auto_error based on auth settings
        self.security = HTTPBearer(auto_error=not self._worker_base.disable_auth)
        # Event store for resumability
        self.event_store: EventStore = event_store or InMemoryEventStore()

    def startup(self) -> None:
        """Start the background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_inactive_sessions())
        # Start MCP server managers
        self._mcp_start_task = asyncio.create_task(self.mcp_server.start())

    async def shutdown(self) -> None:
        """Stop the background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        # Stop MCP server managers
        await self.mcp_server.stop()

    async def _cleanup_inactive_sessions(self) -> None:  # noqa: C901
        """Periodically clean up sessions that have timed out."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval_sec)
                now = time.time()

                # Collect sessions to remove while holding the lock
                sessions_to_close = []
                async with self.sessions_lock:
                    # Check if we're over the session limit
                    if len(self.sessions) > self.max_sessions:
                        # Remove oldest sessions first
                        sorted_sessions = sorted(
                            self.sessions.items(), key=lambda x: x[1].last_active
                        )
                        excess_count = len(self.sessions) - self.max_sessions
                        for session_id, session in sorted_sessions[:excess_count]:
                            sessions_to_close.append((session_id, session))

                    # Remove inactive sessions
                    for sid, session in self.sessions.items():
                        if now - session.last_active > self.session_timeout_sec:
                            sessions_to_close.append((sid, session))

                # Close and remove sessions outside the lock to avoid blocking
                for session_id, session in sessions_to_close:
                    try:
                        await session.close()
                    except Exception as e:
                        logger.warning(f"Error closing session {session_id}: {e}")

                    async with self.sessions_lock:
                        self.sessions.pop(session_id, None)

                    # Clean up write stream
                    if session_id in self.mcp_server.write_streams:
                        del self.mcp_server.write_streams[session_id]

            except asyncio.CancelledError:
                # Cleanup task cancelled during shutdown
                break
            except Exception as e:
                logger.debug(f"Error in session cleanup: {e}")
                # Continue running even if there's an error

    def register(self, router: Router) -> None:
        """
        Register the MCP SSE route with the router.
        """
        from arcade_serve.core.common import RouteSpec

        # Register GET endpoint (SSE stream)
        get_spec = RouteSpec(
            path="/mcp",
            methods=["GET"],
            endpoint=self.__call__,
            response_class=EventSourceResponse,
            operation_id="mcp_sse",
            description="MCP Server-Sent Events",
            summary="MCP Server-Sent Events",
            tags=["MCP"],
        )
        router.add_raw_route(get_spec)

        # Register POST endpoint (request processing / initialize)
        auth_dependency = self.get_auth_dependency()
        post_spec = RouteSpec(
            path="/mcp",
            methods=["POST"],
            endpoint=self.__call__,
            dependencies=[Depends(auth_dependency)] if not self._worker_base.disable_auth else [],
            operation_id="mcp_post",
            description="MCP POST",
            summary="MCP POST",
            tags=["MCP"],
        )
        router.add_raw_route(post_spec)

    def get_auth_dependency(self) -> Callable:
        async def dependency(
            credentials: HTTPAuthorizationCredentials | None = Depends(self.security),
        ) -> None:
            if not self._worker_base.disable_auth and credentials:
                await validate_engine_request(self._worker_base.secret, credentials)

        return dependency

    async def __call__(  # type: ignore[override]  # noqa: C901
        self, request: Request
    ) -> EventSourceResponse | dict[str, str] | JSONResponse:
        """
        Handle the request to get the catalog.
        """
        # Protocol version validation
        protocol_version = request.headers.get("mcp-protocol-version")
        if protocol_version is not None and protocol_version != LATEST_PROTOCOL_VERSION:
            return {
                "status": "error",
                "message": f"Unsupported protocol version: {protocol_version}",
            }

        if request.method == "GET":
            session_id = str(uuid.uuid4())
            session = TransportSession(session_id, max_queue_size=self.max_queue)

            # Add session with lock
            async with self.sessions_lock:
                self.sessions[session_id] = session

            last_event_id = request.headers.get("last-event-id")

            async def event_generator() -> Any:  # noqa: C901
                try:
                    # On initial connect, emit session id as header-equivalent event
                    yield {
                        "event": "session_id",
                        "data": json.dumps({"session_id": session_id}),
                    }

                    # If client supplied Last-Event-ID, attempt replay
                    if last_event_id:
                        try:
                            past = await self.event_store.replay_events_after(
                                session_id, last_event_id
                            )
                            for eid, msg in past:
                                yield {"id": eid, "data": json.dumps(msg)}
                        except Exception:
                            logger.debug("Replay failed; continuing with live stream")

                    # Stream messages from the session's queue
                    while True:
                        try:
                            # Use timeout to periodically check connection health
                            message = await asyncio.wait_for(session.queue.get(), timeout=30.0)
                            if message is None:  # Sentinel value
                                break
                            # Store and emit event id
                            try:
                                event_id = await self.event_store.store_event(session_id, message)
                                yield {"id": event_id, "data": json.dumps(message)}
                            except Exception:
                                # Fallback without id
                                yield {"data": json.dumps(message)}
                        except asyncio.TimeoutError:
                            # Send keepalive ping
                            yield {"event": "ping", "data": "{}"}
                            continue
                except asyncio.CancelledError:
                    # Client disconnected
                    pass
                except Exception as e:
                    # Log unexpected errors
                    logger.debug(f"SSE stream error: {e}")
                    yield {
                        "event": "error",
                        "data": json.dumps({
                            "error": "Internal server error",
                            "status": "error",
                        }),
                    }
                finally:
                    # Clean up session on disconnect
                    async with self.sessions_lock:
                        if session_id in self.sessions:
                            del self.sessions[session_id]

                    # Clean up write stream
                    if session_id in self.mcp_server.write_streams:
                        del self.mcp_server.write_streams[session_id]

            return EventSourceResponse(event_generator(), headers={"mcp-session-id": session_id})
        elif request.method == "POST":
            try:
                # Validate content type
                content_type = request.headers.get("content-type", "")
                if not content_type.startswith("application/json"):
                    return {
                        "status": "error",
                        "message": "Content-Type must be application/json",
                    }

                # Parse request body with size limit
                body_bytes = await request.body()
                if len(body_bytes) > 1024 * 1024:  # 1MB limit
                    return {"status": "error", "message": "Request body too large (max 1MB)"}

                try:
                    body = json.loads(body_bytes)
                except json.JSONDecodeError as e:
                    return {"status": "error", "message": f"Invalid JSON: {e!s}"}

                session_id = request.headers.get("mcp-session-id") or ""

                if body.get("method") == "initialize":
                    # Tolerate missing params by injecting minimal InitializeParams
                    if "params" not in body or not isinstance(body.get("params"), dict):
                        body["params"] = {
                            "protocolVersion": LATEST_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "client", "version": "1.0.0"},
                        }

                    session_id = str(uuid.uuid4())
                    session = TransportSession(session_id, max_queue_size=self.max_queue)

                    # Add session with lock
                    async with self.sessions_lock:
                        self.sessions[session_id] = session

                    # Create write stream adapter for SSE
                    class SSEWriteStream:
                        def __init__(self, session: TransportSession):
                            self.session = session

                        async def send(self, message: str) -> None:
                            # Parse message and put in queue
                            try:
                                if isinstance(message, str):  # noqa: SIM108
                                    data = json.loads(message)
                                else:
                                    data = message
                                await self.session.queue.put(data)
                            except Exception:
                                logger.exception("Error sending SSE notification")

                    # Register write stream with MCP server
                    self.mcp_server.write_streams[session_id] = SSEWriteStream(session)

                    try:
                        response = await self.mcp_server.handle_message(body, user_id=session_id)
                        if response:
                            await session.queue.put(response.model_dump(exclude_none=True))
                    except Exception as e:
                        # Clean up session on error
                        async with self.sessions_lock:
                            if session_id in self.sessions:
                                del self.sessions[session_id]
                        # Clean up write stream
                        if session_id in self.mcp_server.write_streams:
                            del self.mcp_server.write_streams[session_id]
                        return {"status": "error", "message": f"Failed to initialize: {e!s}"}

                    return JSONResponse(
                        content={"status": "ok", "session_id": session_id},
                        headers={"mcp-session-id": session_id},
                    )

                # Get session with lock
                async with self.sessions_lock:
                    if not session_id or session_id not in self.sessions:
                        return {"status": "error", "message": "Invalid or expired session ID"}
                    session = self.sessions[session_id]

                session.touch()

                try:
                    response = await self.mcp_server.handle_message(body, user_id=session_id)
                    if response:
                        await session.queue.put(response.model_dump(exclude_none=True))
                except Exception as e:
                    logger.debug(f"Error processing request: {e}")
                    return {
                        "status": "error",
                        "message": f"Failed to process request: {e!s}",
                    }

                return {"status": "ok"}  # noqa: TRY300

            except Exception as e:
                logger.debug(f"Error processing request: {e}")
                return {"status": "error", "message": f"Failed to process request: {e!s}"}
        # Default error for unsupported methods
        return {"status": "error", "message": "Method not allowed"}
