"""Main Server class for arcade-mcp."""

# standard library
import asyncio
import logging
from typing import Any, Callable

import uvicorn
from arcade_core.catalog import ToolCatalog
from arcade_core.toolkit import Toolkit
from arcade_serve.fastapi.sse import SSEComponent
from arcade_serve.fastapi.streamable import StreamableHTTPComponent
from arcade_serve.fastapi.worker import FastAPIWorker
from arcade_serve.mcp.stdio import StdioServer
from fastapi import FastAPI

from arcade_mcp.config import (
    MCPConfig,
    ServerConfig,
    ServerContext,
    ServerToolkitMetadata,
    init_server_toolkit,
)

logger = logging.getLogger(__name__)


class Server:
    """
    MCP Server that can run with different transports.

    Example:
        from arcade_mcp import Server
        from my_toolkit import tools

        server = Server()
        server.add_toolkit(tools)

        if __name__ == "__main__":
            server.run(transport="streamable-http")
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        version: str | None = None,
        auth_disabled: bool = True,
        local_context: ServerContext | dict[str, Any] | None = None,
        app: FastAPI | None = None,
        log_level: str = "INFO",
        rate_limit_per_min: int = 60,
        debounce_ms: int = 100,
        max_queue: int = 1000,
        session_timeout_sec: int = 300,
        cleanup_interval_sec: int = 10,
        max_sessions: int = 1000,
        arcade_api_key: str | None = None,
        arcade_api_url: str | None = None,
        enable_logging: bool | None = None,
    ):
        """
        Initialize the MCP server.

        Args:
            name: Server name (overrides pyproject/env)
            version: Server version (overrides pyproject/env)
            auth_disabled: Whether to disable authentication (default: True for local development)
            local_context: Local context configuration (typed ServerContext or dict)
            app: FastAPI app to use
        """
        self.auth_disabled = auth_disabled

        # Normalize local context to ServerContext
        # TODO make this more robust
        if isinstance(local_context, ServerContext):
            self.local_context = local_context
        elif isinstance(local_context, dict):
            self.local_context = ServerContext(**local_context)
        else:
            self.local_context = ServerContext()

        # Build toolkit from pyproject/.env; allow explicit overrides via ctor
        toolkit = init_server_toolkit()
        self.name = name or toolkit.name
        self.version = version or toolkit.version

        self.app = app
        self.catalog = ToolCatalog()

        # Construct typed configuration using merged sources
        self.config = MCPConfig(
            server=ServerConfig(
                log_level=log_level,
                rate_limit_per_min=rate_limit_per_min,
                debounce_ms=debounce_ms,
                max_queue=max_queue,
                cleanup_interval_sec=cleanup_interval_sec,
                max_sessions=max_sessions,
                session_timeout_sec=session_timeout_sec,
            ),
            tool_metadata=ServerToolkitMetadata(
                name=self.name,
                version=self.version,
                description=toolkit.description,
                package_name=toolkit.package_name,
                author=toolkit.author,
                repository=toolkit.repository,
                homepage=toolkit.homepage,
            ),
            enable_logging=enable_logging if enable_logging is not None else True,
            arcade_api_key=arcade_api_key,
            arcade_api_url=arcade_api_url,
            context=self.local_context,
        )

    def add_toolkit(self, toolkit: Toolkit) -> None:
        """
        Add a toolkit to the server.

        Args:
            toolkit: The toolkit to add
        """
        self.catalog.add_toolkit(toolkit)

    def add_tool(self, tool: Callable, metadata: ServerToolkitMetadata | None = None) -> None:
        """
        Add a single tool to the server using ServerToolkitMetadata.

        If no metadata is provided, a Toolkit will be synthesized using the
        server identity and pyproject metadata when available.

        Args:
            tool: The tool function to add
            metadata: Optional ServerToolkitMetadata for toolkit attribution
        """
        tk_meta: ServerToolkitMetadata = metadata or self.config.tool_metadata
        # Using catalog API directly with toolkit name ensures it's grouped correctly
        self.catalog.add_tool(tool, tk_meta.name)

    def run(
        self,
        transport: str = "streamable-http",
        host: str = "0.0.0.0",  # noqa: S104
        port: int = 8000,
        reload: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Run the server with the specified transport.

        Args:
            transport: Transport type ("streamable-http", "sse", or "stdio")
            host: Host to bind to (for stream/sse transports)
            port: Port to bind to (for stream/sse transports)
            reload: Auto-reload for HTTP transports
            **kwargs: Additional transport-specific options
        """
        # Normalize log level once here for uvicorn
        normalized_log_level = str(self.config.server.log_level).lower()

        context_dict = self.config.context.model_dump()

        if transport == "stdio":
            stdio_server = StdioServer(
                self.catalog,
                auth_disabled=self.auth_disabled,
                local_context=context_dict,
            )
            asyncio.run(stdio_server.run())
            return

        # If not stdio, we need an HTTP server
        # currently we default to using a FASTAPI app
        if self.app is None:
            self.app = FastAPI(
                title=self.name,
                description="Arcade MCP server",
                version=self.version,
                openapi_url="/openapi.json",
                docs_url="/docs",
                redoc_url="/redoc",
            )

        # Create worker
        worker = FastAPIWorker(
            app=self.app,
            disable_auth=self.auth_disabled,
        )

        # Ensure the component sees the populated catalog
        worker.catalog = self.catalog

        # Shared component kwargs
        component_kwargs: dict[str, Any] = {
            "local_context": context_dict,
            "session_timeout_sec": kwargs.pop(
                "session_timeout_sec", self.config.server.session_timeout_sec
            ),
            "cleanup_interval_sec": kwargs.pop(
                "cleanup_interval_sec", self.config.server.cleanup_interval_sec
            ),
            "max_sessions": kwargs.pop("max_sessions", self.config.server.max_sessions),
            "max_queue": kwargs.pop("max_queue", self.config.server.max_queue),
            "log_level": normalized_log_level,
            "rate_limit_per_min": kwargs.pop(
                "rate_limit_per_min", self.config.server.rate_limit_per_min
            ),
            "debounce_ms": kwargs.pop("debounce_ms", self.config.server.debounce_ms),
            "server_name": kwargs.pop("server_name", self.name),
            "server_version": kwargs.pop("server_version", self.version),
            "server_title": kwargs.pop("server_title", self.name),
        }

        # Register appropriate component
        if transport == "sse":
            worker.register_component(SSEComponent, **component_kwargs)
        elif transport == "streamable-http":
            worker.register_component(
                StreamableHTTPComponent,
                **{
                    k: v
                    for k, v in component_kwargs.items()
                    if k
                    in {
                        "local_context",
                        "log_level",
                        "rate_limit_per_min",
                        "debounce_ms",
                        "server_name",
                        "server_version",
                        "server_title",
                    }
                },
            )
        else:
            raise ValueError(
                f"Unknown transport '{transport}'. Choose from: stdio, sse, streamable-http"
            )

        # Run with uvicorn
        uvicorn.run(
            self.app,
            host=host,
            port=port,
            reload=reload,
            log_level=normalized_log_level,
            **kwargs,
        )
