"""
MCP Server Implementation

Provides request handling, middleware orchestration, and manager-backed
operations for tools, resources, prompts, sampling, logging, and roots.

Key notes:
- For every incoming request, a new MCP ModelContext is created and set as
  current via a ContextVar for the request lifetime
- Tool invocations receive a ToolContext (wrapped by TDK as needed) and are
  executed via ToolExecutor
- Managers (tool, resource, prompt) back the namespaced operations
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Callable

from arcade_core.catalog import MaterializedTool, ToolCatalog
from arcade_core.config import config
from arcade_core.executor import ToolExecutor
from arcade_core.schema import ToolContext
from arcade_tdk.context import Context as TDKContext
from arcadepy import ArcadeError, AsyncArcade
from arcadepy.types.auth_authorize_params import AuthRequirement, AuthRequirementOauth2

from arcade_mcp.base import MCPComponent
from arcade_mcp.context import Context, get_current_model_context, set_current_model_context
from arcade_mcp.convert import convert_to_mcp_content
from arcade_mcp.exceptions import NotFoundError, ToolError
from arcade_mcp.lifespan import LifespanManager
from arcade_mcp.managers import PromptManager, ResourceManager, ToolManager
from arcade_mcp.middleware import (
    CallNext,
    ErrorHandlingMiddleware,
    LoggingMiddleware,
    Middleware,
    MiddlewareContext,
)
from arcade_mcp.session import InitializationState, ServerSession
from arcade_mcp.settings import MCPSettings
from arcade_mcp.types import (
    LATEST_PROTOCOL_VERSION,
    CallToolRequest,
    CallToolResult,
    CompleteRequest,
    CreateMessageRequest,
    ElicitRequest,
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
    ListRootsRequest,
    ListToolsRequest,
    ListToolsResult,
    PingRequest,
    ReadResourceRequest,
    ReadResourceResult,
    ServerCapabilities,
    SetLevelRequest,
    SubscribeRequest,
    UnsubscribeRequest,
)

logger = logging.getLogger("arcade.mcp")


class MCPServer(MCPComponent):
    """
    MCP Server with middleware and context support.

    This server provides:
    - Middleware chain for extensible request processing
    - Context injection for tools
    - Component managers for tools, resources, and prompts
    - Bidirectional communication support to MCP clients
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        *,
        name: str = "ArcadeMCP",
        version: str = "0.1.0",
        title: str | None = None,
        instructions: str | None = None,
        settings: MCPSettings | None = None,
        middleware: list[Middleware] | None = None,
        lifespan: Callable[[Any], Any] | None = None,
        auth_disabled: bool = False,
        arcade_api_key: str | None = None,
        arcade_api_url: str | None = None,
    ):
        """
        Initialize MCP server.

        Args:
            catalog: Tool catalog
            name: Server name
            version: Server version
            title: Server title for display
            instructions: Server instructions
            settings: MCP settings (uses env if not provided)
            middleware: List of middleware to apply
            lifespan: Lifespan manager function
            auth_disabled: Disable authentication
            arcade_api_key: Arcade API key (overrides settings)
            arcade_api_url: Arcade API URL (overrides settings)
        """
        super().__init__(name)

        # Server identity
        self.version = version
        self.title = title or name
        self.instructions = instructions or self._default_instructions()

        # Settings
        self.settings = settings or MCPSettings.from_env()
        self.auth_disabled = auth_disabled or self.settings.arcade.auth_disabled

        # Initialize Arcade client
        self._init_arcade_client(
            arcade_api_key or self.settings.arcade.api_key,
            arcade_api_url or self.settings.arcade.api_url,
        )

        # Component managers (passive)
        self._tool_manager = ToolManager(catalog=catalog)
        self._resource_manager = ResourceManager()
        self._prompt_manager = PromptManager()

        # Middleware chain
        self.middleware: list[Middleware] = []
        self._init_middleware(middleware)

        # Lifespan management
        self.lifespan_manager = LifespanManager(self, lifespan)

        # Session management
        self._sessions: dict[str, ServerSession] = {}
        self._sessions_lock = asyncio.Lock()

        # Server lifecycle lock
        self._lifecycle_lock = asyncio.Lock()
        self._started: bool = False

        # Handler registration
        self._handlers = self._register_handlers()

    def _init_arcade_client(self, api_key: str | None, api_url: str | None) -> None:
        """Initialize Arcade client for runtime authorization."""
        self.arcade: AsyncArcade | None = None

        if not api_key:
            api_key = os.environ.get("ARCADE_API_KEY")
        if not api_url:
            api_url = os.environ.get("ARCADE_API_URL", "https://api.arcade.dev")

        if api_key:
            self.logger.info(f"Using Arcade client with API URL: {api_url}")
            self.arcade = AsyncArcade(api_key=api_key, base_url=api_url)
        else:
            self.logger.warning(
                "Arcade API key not configured. Tools requiring auth will return a login instruction."
            )

    def _init_middleware(self, custom_middleware: list[Middleware] | None) -> None:
        """Initialize middleware chain."""
        # Always add error handling first (innermost)
        self.middleware.append(
            ErrorHandlingMiddleware(mask_error_details=self.settings.middleware.mask_error_details)
        )

        # Add logging if enabled
        if self.settings.middleware.enable_logging:
            self.middleware.append(LoggingMiddleware(log_level=self.settings.middleware.log_level))

        # Add custom middleware
        if custom_middleware:
            self.middleware.extend(custom_middleware)

    def _register_handlers(self) -> dict[str, Callable]:
        """Register method handlers."""
        return {
            "ping": self._handle_ping,
            "initialize": self._handle_initialize,
            "tools/list": self._handle_list_tools,
            "tools/call": self._handle_call_tool,
            "resources/list": self._handle_list_resources,
            "resources/templates/list": self._handle_list_resource_templates,
            "resources/read": self._handle_read_resource,
            "prompts/list": self._handle_list_prompts,
            "prompts/get": self._handle_get_prompt,
            "logging/setLevel": self._handle_set_log_level,
        }

    def _default_instructions(self) -> str:
        """Get default server instructions."""
        return (
            "The Arcade MCP Server provides access to tools defined in Arcade toolkits. "
            "Use 'tools/list' to see available tools and 'tools/call' to execute them."
        )

    async def _start(self) -> None:
        """Start server components (idempotent, guarded by server lock)."""
        async with self._lifecycle_lock:
            if self._started:
                return
            await self.lifespan_manager.startup()
            self._started = True

    async def _stop(self) -> None:
        """Stop server components (idempotent, guarded by server lock)."""
        async with self._lifecycle_lock:
            if not self._started:
                return

            # Stop all sessions
            async with self._sessions_lock:
                sessions = list(self._sessions.values())
            for session in sessions:
                # Sessions should handle their own cleanup
                pass

            # Managers are passive; no per-manager stop needed

            # Stop lifespan
            await self.lifespan_manager.shutdown()
            self._started = False

    async def run_connection(
        self,
        read_stream: Any,
        write_stream: Any,
        init_options: Any = None,
    ) -> None:
        """
        Run a single MCP connection.

        Args:
            read_stream: Stream for reading messages
            write_stream: Stream for writing messages
            init_options: Connection initialization options
        """

        # Create session
        session = ServerSession(
            server=self,
            read_stream=read_stream,
            write_stream=write_stream,
            init_options=init_options,
        )

        # Register session
        async with self._sessions_lock:
            self._sessions[session.session_id] = session

        try:
            self.logger.info(f"Starting session {session.session_id}")
            await session.run()
        except Exception as e:
            self.logger.error(f"Session error: {e}")
            raise
        finally:
            # Unregister session
            async with self._sessions_lock:
                self._sessions.pop(session.session_id, None)
            self.logger.info(f"Session {session.session_id} ended")

    def _extract_local_user_id(self) -> str:
        """Extract user ID from various sources."""

        if self.settings.arcade.development:
            if self.settings.arcade.dev_user_id:
                return self.settings.arcade.dev_user_id
            elif config.user and config.user.email:
                return config.user.email
            else:
                return str(uuid.uuid4())

    async def handle_message(
        self,
        message: Any,
        session: ServerSession | None = None,
    ) -> Any:
        """
        Handle an incoming message.

        Args:
            message: Message to handle
            session: Server session

        Returns:
            Response message or None
        """
        # Validate message
        if not isinstance(message, dict):
            return JSONRPCError(
                id="null",
                error={"code": -32600, "message": "Invalid request"},
            )

        method = message.get("method")
        msg_id = message.get("id")

        # Handle notifications (no response needed)
        if method and method.startswith("notifications/"):
            if method == "notifications/initialized" and session:
                session.mark_initialized()
            return None

        # Check if this is a response to a server-initiated request
        if "id" in message and "method" not in message:
            # This is handled in the session's message processing
            return None

        # Check initialization state
        if session and session.initialization_state != InitializationState.INITIALIZED:
            if method not in ["initialize", "ping"]:
                return JSONRPCError(
                    id=str(msg_id or "null"),
                    error={
                        "code": -32600,
                        "message": "Request not allowed before initialization",
                    },
                )

        # Find handler
        handler = self._handlers.get(method)
        if not handler:
            return JSONRPCError(
                id=str(msg_id or "null"),
                error={"code": -32601, "message": f"Method not found: {method}"},
            )

        # Create context and apply middleware
        try:
            # Create request context
            context = (
                await session.create_request_context()
                if session
                else Context(self, request_id=str(msg_id) if msg_id else None)
            )

            # Set as current model context
            token = set_current_model_context(context)

            try:
                # Create middleware context
                middleware_context = MiddlewareContext(
                    message=message,
                    mcp_context=context,
                    source="client",
                    type="request",
                    method=method,
                    request_id=str(msg_id) if msg_id else None,
                    session_id=session.session_id if session else None,
                )

                # Parse message based on method
                parsed_message = self._parse_message(message, method)

                # Apply middleware chain
                result = await self._apply_middleware(
                    middleware_context, lambda ctx: handler(parsed_message, session=session)
                )

                return result

            finally:
                # Clean up context
                set_current_model_context(None, token)
                if session:
                    await session.cleanup_request_context(context)

        except Exception as e:
            self.logger.exception(f"Error handling message: {e}")
            return JSONRPCError(
                id=str(msg_id or "null"),
                error={"code": -32603, "message": "Internal error"},
            )

    def _parse_message(self, message: dict[str, Any], method: str) -> Any:
        """Parse raw message dict into typed message based on method."""
        message_types = {
            "ping": PingRequest,
            "initialize": InitializeRequest,
            "tools/list": ListToolsRequest,
            "tools/call": CallToolRequest,
            "resources/list": ListResourcesRequest,
            "resources/read": ReadResourceRequest,
            "resources/subscribe": SubscribeRequest,
            "resources/unsubscribe": UnsubscribeRequest,
            "resources/templates/list": ListResourceTemplatesRequest,
            "prompts/list": ListPromptsRequest,
            "prompts/get": GetPromptRequest,
            "logging/setLevel": SetLevelRequest,
            "sampling/createMessage": CreateMessageRequest,
            "completion/complete": CompleteRequest,
            "roots/list": ListRootsRequest,
            "elicitation/create": ElicitRequest,
        }

        message_type = message_types.get(method)
        if message_type:
            return message_type.model_validate(message)
        return message

    async def _apply_middleware(
        self,
        context: MiddlewareContext[Any],
        final_handler: Callable[[MiddlewareContext[Any]], Any],
    ) -> Any:
        """Apply middleware chain to a request."""
        # Build chain from outside in
        chain: CallNext[Any, Any] = final_handler

        for middleware in reversed(self.middleware):

            async def make_handler(
                ctx: MiddlewareContext[Any],
                next_handler: CallNext[Any, Any] = chain,
                mw: Middleware = middleware,
            ) -> Any:
                return await mw(ctx, next_handler)

            chain = make_handler

        # Execute chain
        return await chain(context)

    # Handler methods
    async def _handle_ping(
        self,
        message: PingRequest,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[Any]:
        """Handle ping request."""
        return JSONRPCResponse(id=message.id, result={})

    async def _handle_initialize(
        self,
        message: InitializeRequest,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[InitializeResult]:
        """Handle initialize request."""
        if session:
            session.set_client_params(message.params)

        result = InitializeResult(
            protocolVersion=LATEST_PROTOCOL_VERSION,
            capabilities=ServerCapabilities(
                tools={"listChanged": True},
                logging={},
                prompts={"listChanged": True},
                resources={"subscribe": True, "listChanged": True},
            ),
            serverInfo=Implementation(
                name=self.name,
                version=self.version,
                title=self.title,
            ),
            instructions=self.instructions,
        )

        return JSONRPCResponse(id=message.id, result=result)

    async def _handle_list_tools(
        self,
        message: ListToolsRequest,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[ListToolsResult]:
        """Handle list tools request."""
        try:
            tools = await self._tool_manager.list_tools()
            return JSONRPCResponse(id=message.id, result=ListToolsResult(tools=tools))
        except Exception:
            self.logger.exception("Error listing tools")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error listing tools"},
            )

    async def _create_tool_context(
        self, tool: MaterializedTool, session: ServerSession | None = None
    ) -> ToolContext:
        """Create a tool context.

        Returns a TDK Context (subclass of ToolContext) so tools get
        namespaced runtime APIs at runtime while preserving signatures.
        """
        tool_context = TDKContext()

        # secrets
        if tool.definition.requirements:
            # Handle secrets
            if tool.definition.requirements.secrets:
                for secret in tool.definition.requirements.secrets:
                    if secret.key in self.settings.tool_secrets():
                        tool_context.set_secret(
                            secret.key, self.settings.tool_secrets()[secret.key]
                        )
                    elif secret.key in os.environ:
                        tool_context.set_secret(secret.key, os.environ[secret.key])

        # user_id is local ARCADE_USER_ID if development
        if self.settings.arcade.environment == "development":
            tool_context.user_id = self.settings.arcade.dev_user_id
        else:
            tool_context.user_id = session.session_id if session else None

        return tool_context

    async def _handle_call_tool(
        self,
        message: CallToolRequest,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[CallToolResult]:
        """Handle tool call request."""
        tool_name = message.params.name
        input_params = message.params.arguments or {}

        try:
            # Get tool
            tool = await self._tool_manager.get_tool(tool_name)

            # Create tool context
            tool_context = await self._create_tool_context(tool, session)

            # Attach tool_context to current model context for this request
            mctx = get_current_model_context()
            if mctx is not None:
                mctx.set_tool_context(tool_context)  # type: ignore[attr-defined]

            # Handle authorization if required
            if tool.definition.requirements and tool.definition.requirements.authorization:
                auth_result = await self._check_authorization(tool, tool_context.user_id)
                if auth_result.status != "completed":
                    return JSONRPCResponse(
                        id=message.id,
                        result=CallToolResult(content=[{"type": "text", "text": auth_result.url}]),
                    )

            # Execute tool
            result = await ToolExecutor.run(
                func=tool.tool,
                definition=tool.definition,
                input_model=tool.input_model,
                output_model=tool.output_model,
                context=tool_context,
                **input_params,
            )

            # Convert result
            if result.value is not None:
                content = convert_to_mcp_content(result.value)
                return JSONRPCResponse(
                    id=message.id,
                    result=CallToolResult(content=content, isError=False),
                )
            else:
                error = result.error or "Error calling tool"
                return JSONRPCResponse(
                    id=message.id,
                    result=CallToolResult(
                        content=[{"type": "text", "text": str(error)}],
                        isError=True,
                    ),
                )
        except NotFoundError:
            # Match test expectation: return a normal response with isError=True
            return JSONRPCResponse(
                id=message.id,
                result=CallToolResult(
                    content=[{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    isError=True,
                ),
            )
        except Exception:
            self.logger.exception("Error calling tool")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error calling tool"},
            )

    async def _check_authorization(
        self,
        tool: MaterializedTool,
        user_id: str | None = None,
    ) -> Any:
        """Check tool authorization."""
        if not self.arcade:
            raise ToolError(
                "Authorization required but Arcade is not configured. "
                "Run 'arcade login' or set ARCADE_API_KEY."
            )

        req = tool.definition.requirements.authorization
        auth_req = AuthRequirement(
            provider_id=str(req.provider_id),
            provider_type=str(req.provider_type),
        )
        if hasattr(req, "oauth2") and req.oauth2:
            auth_req.oauth2 = AuthRequirementOauth2(scopes=req.oauth2.scopes or [])

        try:
            response = await self.arcade.auth.authorize(
                auth_requirement=auth_req,
                user_id=user_id or "anonymous",
            )
            return response
        except ArcadeError as e:
            self.logger.exception("Error authorizing tool")
            raise ToolError(f"Authorization failed: {e}") from e

    async def _handle_list_resources(
        self,
        message: ListResourcesRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[ListResourcesResult]:
        """Handle list resources request."""
        try:
            resources = await self._resource_manager.list_resources()
            return JSONRPCResponse(id=message.id, result=ListResourcesResult(resources=resources))
        except Exception:
            self.logger.exception("Error listing resources")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error listing resources"},
            )

    async def _handle_list_resource_templates(
        self,
        message: ListResourceTemplatesRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[ListResourceTemplatesResult]:
        """Handle list resource templates request."""
        try:
            templates = await self._resource_manager.list_resource_templates()
            return JSONRPCResponse(
                id=message.id,
                result=ListResourceTemplatesResult(resourceTemplates=templates),
            )
        except Exception:
            self.logger.exception("Error listing resource templates")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error listing resource templates"},
            )

    async def _handle_read_resource(
        self,
        message: ReadResourceRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[ReadResourceResult]:
        """Handle read resource request."""
        try:
            contents = await self._resource_manager.read_resource(message.params.uri)
            return JSONRPCResponse(id=message.id, result=ReadResourceResult(contents=contents))
        except NotFoundError:
            return JSONRPCError(
                id=message.id,
                error={"code": -32002, "message": f"Resource not found: {message.params.uri}"},
            )
        except Exception:
            self.logger.exception(f"Error reading resource: {message.params.uri}")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error reading resource"},
            )

    async def _handle_list_prompts(
        self,
        message: ListPromptsRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[ListPromptsResult]:
        """Handle list prompts request."""
        try:
            prompts = await self._prompt_manager.list_prompts()
            return JSONRPCResponse(id=message.id, result=ListPromptsResult(prompts=prompts))
        except Exception:
            self.logger.exception("Error listing prompts")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error listing prompts"},
            )

    async def _handle_get_prompt(
        self,
        message: GetPromptRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[GetPromptResult]:
        """Handle get prompt request."""
        try:
            result = await self._prompt_manager.get_prompt(
                message.params.name,
                message.params.arguments if hasattr(message.params, "arguments") else None,
            )
            return JSONRPCResponse(id=message.id, result=result)
        except NotFoundError:
            return JSONRPCError(
                id=message.id,
                error={"code": -32002, "message": f"Prompt not found: {message.params.name}"},
            )
        except Exception:
            self.logger.exception(f"Error getting prompt: {message.params.name}")
            return JSONRPCError(
                id=message.id,
                error={"code": -32603, "message": "Internal error getting prompt"},
            )

    async def _handle_set_log_level(
        self,
        message: SetLevelRequest,
        user_id: str | None = None,
        session: ServerSession | None = None,
    ) -> JSONRPCResponse[Any]:
        """Handle set log level request."""
        try:
            level_name = str(
                message.params.level.value
                if hasattr(message.params.level, "value")
                else message.params.level
            )
            self.logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
        except Exception:
            self.logger.setLevel(logging.INFO)

        return JSONRPCResponse(id=message.id, result={})

    # Resource support for Context
    async def _mcp_read_resource(self, uri: str) -> list[Any]:
        """Read a resource (for Context.read_resource)."""
        return await self._resource_manager.read_resource(uri)
