import asyncio
import json
import logging
import os
import uuid
from enum import Enum
from typing import Any, Callable, cast

from arcade_core.catalog import MaterializedTool, ToolCatalog
from arcade_core.executor import ToolExecutor
from arcade_core.schema import ToolAuthorizationContext, ToolContext, ToolMetadataItem
from arcade_tdk import LogLevel, create_tool_logger, create_tool_notifier
from arcadepy import ArcadeError, AsyncArcade
from arcadepy.types.auth_authorize_params import (
    AuthRequirement,
    AuthRequirementOauth2,
)
from arcadepy.types.shared import AuthorizationResponse

from arcade_serve.mcp.convert import convert_to_mcp_content, create_mcp_tool
from arcade_serve.mcp.logging import create_mcp_logging_middleware
from arcade_serve.mcp.message_processor import (
    create_message_processor,
)
from arcade_serve.mcp.notification_adapter import MCPNotificationBackend
from arcade_serve.mcp.notification_manager import (
    NotificationManager,
)
from arcade_serve.mcp.request_manager import RequestManager
from arcade_serve.mcp.session import ServerSession
from arcade_serve.mcp.types import (
    LATEST_PROTOCOL_VERSION,
    CallToolRequest,
    CallToolResult,
    GetPromptRequest,
    GetPromptResult,
    Implementation,
    InitializeRequest,
    InitializeResult,
    JSONRPCError,
    JSONRPCResponse,
    ListPromptsRequest,
    ListPromptsResult,
    ListResourcesRequest,
    ListResourcesResult,
    ListResourceTemplatesRequest,
    ListResourceTemplatesResult,
    ListToolsRequest,
    ListToolsResult,
    PingRequest,
    ReadResourceRequest,
    ReadResourceResult,
    ServerCapabilities,
    SetLevelRequest,
    Tool,
)

logger = logging.getLogger("arcade.mcp")


class MemoryNotificationBackend:
    """Minimal backend that captures logs/progress in memory (no transport)."""

    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []

    async def send_log(
        self,
        level: LogLevel,
        message: str,
        data: Any = None,
        logger_name: str | None = None,
    ) -> None:
        self.logs.append({
            "level": getattr(level, "value", str(level)),
            "message": message,
            "data": data,
            "logger": logger_name,
        })

    async def send_progress(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        # No-op for now; can capture if needed
        return None

    async def send_resource_updated(self, uri: str, timestamp: str | None = None) -> None:
        return None

    async def send_resource_list_changed(self) -> None:
        return None


class MessageMethod(str, Enum):
    """Enumeration of supported MCP message methods."""

    PING = "ping"
    INITIALIZE = "initialize"
    LIST_TOOLS = "tools/list"
    CALL_TOOL = "tools/call"
    LIST_RESOURCES = "resources/list"
    LIST_RESOURCE_TEMPLATES = "resources/templates/list"
    READ_RESOURCE = "resources/read"
    LIST_PROMPTS = "prompts/list"
    GET_PROMPT = "prompts/get"
    SET_LOG_LEVEL = "logging/setLevel"


class MCPServer:
    """Unified async MCP server.

    - Parses and dispatches JSON-RPC/MCP requests
    - Manages per-connection sessions, notifications, and server→client requests
    - Executes @tool functions with a rich ToolContext (logging, progress, auth, client APIs)
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        auth_disabled: bool = False,
        local_context: dict[str, Any] | None = None,
        arcade_api_key: str | None = None,
        arcade_api_url: str | None = None,
        enable_logging: bool | None = None,
        rate_limit_per_min: int = 60,
        debounce_ms: int = 100,
        log_level: str = "INFO",
        server_name: str = "Arcade MCP Server",
        server_version: str = "0.1.0",
        server_title: str | None = "Arcade MCP Server",
    ):
        self.tool_catalog = catalog
        self.auth_disabled = auth_disabled
        self.local_context = local_context or {}
        # Configure logging middleware
        self.message_processor = create_message_processor(
            create_mcp_logging_middleware(log_level=log_level)
        )
        self._init_arcade_client(arcade_api_key, arcade_api_url)

        self.write_streams: dict[str, Any] = {}
        self.client_log_levels: dict[str, str] = {}
        self.notification_manager: NotificationManager | None = None
        self._notification_rate_limit = rate_limit_per_min
        self._notification_debounce = debounce_ms
        self._init_notification_manager()
        # Identity for initialize
        self._server_name = server_name
        self._server_version = server_version
        self._server_title = server_title

        self.sessions: dict[str, ServerSession] = {}
        self._started = False

        self.dispatch_table: dict[str, Callable] = {
            MessageMethod.PING: self._handle_ping,
            MessageMethod.INITIALIZE: self._handle_initialize,
            MessageMethod.LIST_TOOLS: self._handle_list_tools,
            MessageMethod.CALL_TOOL: self._handle_call_tool,
            MessageMethod.LIST_RESOURCES: self._handle_list_resources,
            MessageMethod.LIST_RESOURCE_TEMPLATES: self._handle_list_resource_templates,
            MessageMethod.READ_RESOURCE: self._handle_read_resource,
            MessageMethod.LIST_PROMPTS: self._handle_list_prompts,
            MessageMethod.GET_PROMPT: self._handle_get_prompt,
            MessageMethod.SET_LOG_LEVEL: self._handle_set_log_level,
        }

    async def start(self) -> None:
        """Start server-wide managers (notifications). Safe to call multiple times."""
        if self._started:
            return
        if self.notification_manager:
            await self.notification_manager.start()
        self._started = True

    async def stop(self) -> None:
        """Stop server-wide managers (notifications)."""
        if not self._started:
            return
        if self.notification_manager and hasattr(self, "_notification_manager_started"):
            await self.notification_manager.stop()
            self._notification_manager_started = False
        self._started = False

    def _init_arcade_client(self, arcade_api_key: str | None, arcade_api_url: str | None) -> None:
        """Initialize Arcade client for runtime authorization.

        Uses AsyncArcade when ARCADE_API_KEY (or explicit key) is present; otherwise
        leaves the client unset. Tools requiring auth will surface a helpful instruction.
        """
        self.arcade: AsyncArcade | None = None

        if not arcade_api_key:
            arcade_api_key = os.environ.get("ARCADE_API_KEY")
        if not arcade_api_url:
            arcade_api_url = os.environ.get("ARCADE_API_URL", "https://api.arcade.dev")

        if arcade_api_key:
            logger.info(f"Using Arcade client with API URL: {arcade_api_url}")
            self.arcade = AsyncArcade(
                api_key=arcade_api_key,
                base_url=arcade_api_url,
            )
        else:
            logger.warning(
                "Arcade API key not configured. Tools requiring auth will return a login instruction."
            )

    def _init_notification_manager(self) -> None:
        """Create the NotificationManager using server write streams."""

        class ServerNotificationSender:
            def __init__(self, server: "MCPServer"):
                self.server = server

            async def send_notification(self, client_id: str, notification: dict[str, Any]) -> bool:
                write_stream = self.server.write_streams.get(client_id)
                if write_stream:
                    try:
                        message = json.dumps(notification)
                        if not message.endswith("\n"):
                            message += "\n"
                        await write_stream.send(message)
                        return True  # noqa: TRY300
                    except Exception as e:
                        logger.debug(f"Failed to send notification to client {client_id}: {e}")
                        return False
                return False

        self.notification_manager = NotificationManager(
            sender=ServerNotificationSender(self),
            rate_limit_per_minute=self._notification_rate_limit,
            default_debounce_ms=self._notification_debounce,
        )

    async def run_connection(
        self,
        read_stream: Any,
        write_stream: Any,
        init_options: Any,
    ) -> None:
        """Run a single MCP connection over the provided read/write streams."""
        user_id = self._get_user_id(init_options)
        self.write_streams[user_id] = write_stream

        session = ServerSession(
            server=self,
            user_id=user_id,
            read_stream=read_stream,
            write_stream=write_stream,
            init_options=init_options,
        )
        self.sessions[user_id] = session

        session._request_manager = RequestManager(write_stream)

        if self.notification_manager and not hasattr(self, "_notification_manager_started"):
            await self.notification_manager.start()
            self._notification_manager_started = True

        try:
            logger.info(f"Starting MCP connection for user {user_id}")

            if self.notification_manager:
                await self.notification_manager.register_client(user_id, [])

            await session.run()

        except asyncio.CancelledError:
            logger.info("Connection cancelled")
        except Exception:
            logger.exception("Error in connection")
        finally:
            if user_id in self.write_streams:
                del self.write_streams[user_id]
            if user_id in self.sessions:
                del self.sessions[user_id]

            if self.notification_manager:
                await self.notification_manager.unregister_client(user_id)

    def _get_user_id(self, init_options: Any) -> str:
        """Resolve a stable user identifier if available, otherwise a UUID."""
        try:
            from arcade_core.config import config

            if config.user and config.user.email:
                return config.user.email
        except ValueError:
            logger.debug("No logged in user for MCP Server")

        fallback = str(uuid.uuid4())
        if os.environ.get("ARCADE_USER_ID", None):
            return os.environ.get("ARCADE_USER_ID", fallback)
        elif isinstance(init_options, dict):
            user_id = init_options.get("user_id")
            if user_id:
                return str(user_id)
        return str(fallback)

    async def _send_response(self, write_stream: Any, response: Any) -> None:
        """Serialize and send a response as a single JSON line."""
        if hasattr(response, "model_dump_json"):
            json_response = response.model_dump_json()
            if not json_response.endswith("\n"):
                json_response += "\n"
            logger.debug(f"Sending response: {json_response[:200]}...")
            await write_stream.send(json_response)
        elif isinstance(response, dict):
            json_response = json.dumps(response)
            if not json_response.endswith("\n"):
                json_response += "\n"
            logger.debug(f"Sending response: {json_response[:200]}...")
            await write_stream.send(json_response)
        else:
            response_str = str(response)
            if not response_str.endswith("\n"):
                response_str += "\n"
            logger.debug(f"Sending raw response type: {type(response)}")
            await write_stream.send(response_str)

    async def handle_message(  # noqa: C901
        self, message: Any, user_id: str | None = None, session: ServerSession | None = None
    ) -> Any:
        """Process an incoming message through middleware and dispatch handlers.

        Also resolves responses for any in-flight server→client requests (RequestManager).
        """
        processed = await self.message_processor.process_request(message)

        # Resolve client responses to server-initiated requests
        if (
            isinstance(processed, dict)
            and processed.get("jsonrpc") == "2.0"
            and ("result" in processed or "error" in processed)
        ):
            if session:
                rm = getattr(session, "_request_manager", None)
                if rm is not None:
                    try:
                        await rm.resolve_response(processed)
                    except Exception:
                        logger.debug(
                            "Failed to resolve client response to server-initiated request"
                        )
            return None

        method = None
        if isinstance(processed, dict):
            method = processed.get("method")
        elif hasattr(processed, "method"):
            method = getattr(processed, "method", None)

        # Notifications do not require responses
        if method and isinstance(method, str) and method.startswith("notifications/"):
            if method == "notifications/initialized" and session is not None:
                session.mark_initialized()
            return None

        if method in self.dispatch_table:
            if isinstance(processed, dict):
                if method == MessageMethod.INITIALIZE:
                    processed = InitializeRequest(**processed)
                elif method == MessageMethod.CALL_TOOL:
                    processed = CallToolRequest(**processed)
                elif method == MessageMethod.LIST_TOOLS:
                    processed = ListToolsRequest(**processed)
                elif method == MessageMethod.PING:
                    processed = PingRequest(**processed)
                elif method == MessageMethod.LIST_RESOURCES:
                    processed = ListResourcesRequest(**processed)
                elif method == MessageMethod.LIST_RESOURCE_TEMPLATES:
                    processed = ListResourceTemplatesRequest(**processed)
                elif method == MessageMethod.READ_RESOURCE:
                    processed = ReadResourceRequest(**processed)
                elif method == MessageMethod.LIST_PROMPTS:
                    processed = ListPromptsRequest(**processed)
                elif method == MessageMethod.GET_PROMPT:
                    processed = GetPromptRequest(**processed)
                elif method == MessageMethod.SET_LOG_LEVEL:
                    processed = SetLevelRequest(**processed)

            if method in [
                MessageMethod.CALL_TOOL,
                MessageMethod.INITIALIZE,
            ]:
                return await self.dispatch_table[method](
                    processed, user_id=user_id, session=session
                )
            return await self.dispatch_table[method](processed)

        return JSONRPCError(
            id=str(
                getattr(
                    processed, "id", processed.get("id") if isinstance(processed, dict) else "0"
                )
            ),
            error={
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        )

    async def shutdown(self) -> None:
        """Shutdown the server and stop the notification manager."""
        self._shutdown = True

        if self.notification_manager and hasattr(self, "_notification_manager_started"):
            await self.notification_manager.stop()
            self._notification_manager_started = False

        logger.info("MCP server shutdown complete")

    async def _handle_ping(self, message: PingRequest) -> JSONRPCResponse[Any]:
        """Respond to ping with an empty result."""
        return JSONRPCResponse(id=message.id, result={})

    async def _handle_initialize(
        self,
        message: InitializeRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[InitializeResult]:
        """Return server capabilities and negotiated protocol version."""
        if session is not None:
            session.set_client_params(message.params)

        result = InitializeResult(
            protocolVersion=LATEST_PROTOCOL_VERSION,
            capabilities=ServerCapabilities(
                tools={"listChanged": True},
                logging={},
                prompts={"listChanged": True},
                resources={"listChanged": True, "subscribe": True},
            ),
            serverInfo=Implementation(
                name=self._server_name,
                version=self._server_version,
                title=self._server_title,
            ),
            instructions=(
                "The Arcade MCP Server provides access to tools defined in Arcade toolkits. "
                "Use 'tools/list' to see available tools and 'tools/call' to execute them."
            ),
        )
        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_list_tools(
        self, message: ListToolsRequest
    ) -> JSONRPCResponse[ListToolsResult] | JSONRPCError:
        """Return available tools as Tool objects (input/output schemas, annotations)."""
        try:
            tools = []
            tool_conversion_errors = []

            for tool in self.tool_catalog:
                try:
                    mcp_tool = create_mcp_tool(tool)
                    if mcp_tool:
                        tools.append(mcp_tool)
                except Exception:
                    tool_name = getattr(tool, "name", str(tool))
                    logger.exception(f"Error converting tool: {tool_name}")
                    tool_conversion_errors.append(tool_name)

            if tool_conversion_errors:
                logger.warning(
                    f"Failed to convert {len(tool_conversion_errors)} tools: {tool_conversion_errors}"
                )

            tool_objects = []
            for t in tools:
                try:
                    tool_dict = dict(t)
                    if "inputSchema" not in tool_dict:
                        tool_dict["inputSchema"] = {
                            "type": "object",
                            "properties": {},
                        }

                    tool_objects.append(Tool(**tool_dict))
                except Exception:
                    logger.exception(f"Error creating Tool object for {t.get('name', 'unknown')}")

            result = ListToolsResult(tools=tool_objects)
            response = JSONRPCResponse(id=message.id, result=result)
        except Exception:
            logger.exception("Error listing tools")
            return JSONRPCError(
                id=message.id,
                error={
                    "code": -32603,
                    "message": "Internal error listing tools",
                },
            )
        return response

    async def _handle_call_tool(  # noqa: C901
        self,
        message: CallToolRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[CallToolResult]:
        """Execute a tool and return an MCP-compliant CallToolResult.

        Injects secrets and runtime authorization (via Arcade) as needed.
        Provides logging/progress and server→client MCP features through ToolContext.
        """
        tool_name: str = message.params.name
        input_params: dict[str, Any] = message.params.arguments or {}

        tool_context = ToolContext()
        self._apply_local_context(tool_context, user_id)

        if session and hasattr(session, "_request_manager"):

            async def _sender(
                method: str, params: dict[str, Any] | None, timeout: float | None
            ) -> Any:
                # resolve attribute safely
                rm = getattr(session, "_request_manager", None)
                if rm is None:
                    raise RuntimeError("Request manager unavailable")
                return await rm.send_request(method, params, timeout)

            tool_context.set_client_request_api(_sender)

        progress_token = None
        if message.params and isinstance(message.params, dict):
            meta = message.params.get("_meta") if isinstance(message.params, dict) else None
            if isinstance(meta, dict) and "progressToken" in meta:
                progress_token = meta["progressToken"]

        if progress_token:
            tool_context.progress_token = progress_token

        # Track memory backend for streamable-http so we can embed logs
        memory_backend: MemoryNotificationBackend | None = None

        if self.notification_manager and user_id:
            min_log_level = self.client_log_levels.get(user_id, "info")

            backend = MCPNotificationBackend(
                self.notification_manager, user_id, min_log_level=min_log_level
            )
            logger_instance = create_tool_logger(backend, tool_name)
            notifier_instance = create_tool_notifier(backend, progress_token)
            tool_context.set_notification_support(
                logger_instance,
                cast(Any, notifier_instance),
            )
            tool_context.set_min_log_level(min_log_level)
        else:
            # Fallback: capture logs locally so they can be embedded in the response
            memory_backend = MemoryNotificationBackend()
            logger_instance = create_tool_logger(memory_backend, tool_name)
            notifier_instance = create_tool_notifier(memory_backend, progress_token)
            tool_context.set_notification_support(
                logger_instance,
                cast(Any, notifier_instance),
            )

        try:
            tool = self.tool_catalog.get_tool_by_name(tool_name, separator="_")
        except Exception:
            return JSONRPCResponse(
                id=message.id,
                result=CallToolResult(
                    content=[{"type": "text", "text": f"Unknown tool: {tool_name}"}], isError=True
                ),
            )

        if tool.definition.requirements and tool.definition.requirements.secrets:
            self._setup_tool_secrets(tool, tool_context)

        requirement = self._get_auth_requirement(tool)
        if requirement and not self.auth_disabled:
            try:
                auth_result = await self._check_authorization(
                    requirement, user_id=user_id or self.local_context.get("user_id")
                )
            except Exception:
                hint = (
                    "Authorization required but Arcade is not configured. "
                    "Run 'arcade login' or set ARCADE_API_KEY to enable auth-required tools."
                )
                return JSONRPCResponse(
                    id=message.id,
                    result=CallToolResult(content=[{"type": "text", "text": hint}], isError=True),
                )

            if auth_result.status != "completed":
                return JSONRPCResponse(
                    id=message.id,
                    result=CallToolResult(content=[{"type": "text", "text": auth_result.url}]),
                )
            else:
                tool_context.authorization = ToolAuthorizationContext(
                    token=auth_result.context.token if auth_result.context else None,
                    user_info={"user_id": user_id} if user_id else {},
                )

        result = await ToolExecutor.run(
            func=tool.tool,
            definition=tool.definition,
            input_model=tool.input_model,
            output_model=tool.output_model,
            context=tool_context,
            **input_params,
        )

        # Prepare logs for embedding into _meta
        meta_logs: list[dict[str, Any]] | None = None
        if result.logs:
            try:
                meta_logs = [
                    {
                        "level": getattr(log, "level", None),
                        "message": getattr(log, "message", None),
                    }
                    for log in result.logs
                ]
            except Exception:
                # Best effort; do not fail call if logs cannot be serialized
                meta_logs = None
        elif memory_backend and memory_backend.logs:
            # Fallback to captured memory logs when executor didn't provide logs
            meta_logs = memory_backend.logs

        if result.value is not None or (result.value is not None and not result.error):
            structured_content: dict[str, Any] | None = None

            require_structured = bool(
                getattr(tool.definition, "output", None)
                and getattr(tool.definition.output, "value_schema", None) is not None
            )

            if isinstance(result.value, dict):
                structured_content = result.value.copy()
            elif require_structured:
                structured_content = {"result": result.value}

            # Ensure logs are visible to clients that don't render _meta
            if meta_logs:
                if structured_content is None:
                    structured_content = {"result": result.value}
                structured_content["logs"] = meta_logs

            if structured_content is not None:
                try:
                    content = [{"type": "text", "text": json.dumps(structured_content)}]
                except Exception:
                    content = convert_to_mcp_content(result.value)
            else:
                content = convert_to_mcp_content(result.value)

            response = JSONRPCResponse(
                id=message.id,
                result=CallToolResult(
                    content=content,
                    structuredContent=structured_content,
                    isError=False,
                    **({"_meta": {"logs": meta_logs}} if meta_logs else {}),
                ),
            )

            if result.logs:
                for log in result.logs:
                    logger.log(
                        getattr(logging, log.level.upper(), logging.INFO),
                        f"Tool log: {log.message}",
                    )

            return response
        else:
            error = result.error or "Error calling tool"
            logger.error(f"Tool {tool_name} returned error: {error}")
            return JSONRPCResponse(
                id=message.id,
                result=CallToolResult(
                    content=[{"type": "text", "text": str(error)}],
                    isError=True,
                    **({"_meta": {"logs": meta_logs}} if meta_logs else {}),
                ),
            )

    def _setup_tool_secrets(self, tool: Any, tool_context: ToolContext) -> None:
        """Inject environment-provided secrets required by the tool."""
        for secret in tool.definition.requirements.secrets:
            value = os.environ.get(secret.key)
            if value is not None:
                tool_context.set_secret(secret.key, value)

    def _apply_local_context(self, tool_context: ToolContext, user_id: str | None = None) -> None:
        """Apply environment/local metadata to ToolContext (user_id, email, etc.)."""
        final_user_id = (
            user_id or os.environ.get("ARCADE_USER_ID") or self.local_context.get("user_id")
        )
        if final_user_id:
            tool_context.user_id = final_user_id

        if os.environ.get("ARCADE_USER_EMAIL"):
            if not tool_context.metadata:
                tool_context.metadata = []
            tool_context.metadata.append(
                ToolMetadataItem(
                    key="user_email", value=str(os.environ.get("ARCADE_USER_EMAIL") or "")
                )
            )

        local_metadata = self.local_context.get("metadata", {})
        for key, value in local_metadata.items():
            if not tool_context.metadata:
                tool_context.metadata = []
            existing = next((m for m in tool_context.metadata if m.key == key), None)
            if not existing:
                tool_context.metadata.append(ToolMetadataItem(key=key, value=str(value)))

    def _get_auth_requirement(self, tool: MaterializedTool) -> AuthRequirement | None:
        """Build an AuthRequirement for the tool if it declares authorization."""
        req = tool.definition.requirements.authorization
        if not req:
            return None
        if not req.provider_id and not req.provider_type:
            return None
        if hasattr(req, "oauth2") and req.oauth2:
            return AuthRequirement(
                provider_id=str(req.provider_id),
                provider_type=str(req.provider_type),
                oauth2=AuthRequirementOauth2(scopes=req.oauth2.scopes or []),
            )
        return AuthRequirement(
            provider_id=str(req.provider_id),
            provider_type=str(req.provider_type),
        )

    async def _check_authorization(
        self, auth_requirement: AuthRequirement, user_id: str | None = None
    ) -> AuthorizationResponse:
        """Authorize a tool at runtime using Arcade Cloud."""
        if self.arcade is None:
            raise RuntimeError("Arcade client not configured")
        try:
            response = await self.arcade.auth.authorize(
                auth_requirement=auth_requirement,
                user_id=user_id or "anonymous",
            )
            logger.debug(f"Authorization response: {response}")

        except ArcadeError:
            logger.exception("Error authorizing tool")
            raise
        return response

    async def _handle_set_log_level(self, message: SetLevelRequest) -> JSONRPCResponse[Any]:
        """Set server log level (affects 'arcade.mcp' logger)."""
        try:
            level_name = str(
                message.params.level.value
                if hasattr(message.params.level, "value")
                else message.params.level
            )
            logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
        except Exception:
            # If anything goes wrong, default to INFO
            logger.setLevel(logging.INFO)
        return JSONRPCResponse(id=message.id, result={})

    # -------------------------------------------------------------------------
    # Stub handlers for resources, and prompts (TODO: implement)
    # -------------------------------------------------------------------------

    async def _handle_list_resources(
        self, message: ListResourcesRequest
    ) -> JSONRPCResponse[ListResourcesResult]:
        """List available resources (stubbed: returns empty list)."""
        result = ListResourcesResult(resources=[])
        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_list_resource_templates(
        self, message: ListResourceTemplatesRequest
    ) -> JSONRPCResponse[ListResourceTemplatesResult]:
        """List available resource templates (stubbed: returns empty list)."""
        result = ListResourceTemplatesResult(resourceTemplates=[])
        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_read_resource(
        self, message: ReadResourceRequest
    ) -> JSONRPCResponse[ReadResourceResult]:
        """Read a resource by URI (stubbed: returns empty contents)."""
        result = ReadResourceResult(contents=[])
        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_list_prompts(
        self, message: ListPromptsRequest
    ) -> JSONRPCResponse[ListPromptsResult]:
        """List available prompts (stubbed: returns empty list)."""
        result = ListPromptsResult(prompts=[])
        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_get_prompt(
        self, message: GetPromptRequest
    ) -> JSONRPCResponse[GetPromptResult]:
        """Get a prompt by name (stubbed: returns empty messages)."""
        result = GetPromptResult(description=None, messages=[])
        return JSONRPCResponse(id=message.id, result=result)
