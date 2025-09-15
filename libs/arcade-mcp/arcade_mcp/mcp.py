"""
MCPApp - A FastAPI-like interface for MCP servers.

Provides a clean, minimal API for building MCP servers with lazy initialization.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, TypeVar
from arcade_mcp.worker import run_arcade_mcp

from arcade_core.catalog import ToolCatalog
from arcade_mcp.exceptions import TransportError
from arcade_tdk import Toolkit
from arcade_tdk.tool import tool as tool_decorator
from loguru import logger
from typing import Literal

P = TypeVar("P")
T = TypeVar("T")

TransportType = Literal["http", "stdio"]

class MCPApp:
    """
    A FastAPI-like interface for building MCP servers.

    The app collects tools and configuration, then lazily creates the server
    and transport when run() is called.

    Example:
        ```python
        from arcade_mcp import MCPApp

        app = MCPApp(name="my_server", version="1.0.0")

        @app.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        app.run(host="127.0.0.1", port=7777)
        ```
    """

    def __init__(
        self,
        name: str = "ArcadeMCP",
        version: str = "1.0.0dev",
        title: str | None = None,
        instructions: str | None = None,
        log_level: str = "INFO",
        transport: TransportType = "http",
        host: str = "127.0.0.1",
        port: int = 7777,
        reload: bool = False,
        **kwargs: Any,
    ):
        """
        Initialize the MCP app.

        Args:
            name: Server name
            version: Server version
            title: Server title for display
            instructions: Server instructions
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
            transport: Transport type ("http")
            host: Host for transport
            port: Port for transport
            reload: Enable auto-reload for development
            **kwargs: Additional server configuration
        """
        self.name = name
        self.version = version
        self.title = title or name
        self.instructions = instructions
        self.log_level = log_level
        self.server_kwargs = kwargs
        self.transport = transport
        self.host = host
        self.port = port
        self.reload = reload

        # Tool collection
        self._catalog = ToolCatalog()
        self._toolkit_name = name

        # Configure logging
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Configure loguru logging."""
        logger.remove()

        # Use appropriate format based on level
        if self.log_level == "DEBUG":
            format_str = "<level>{level: <8}</level> | <green>{time:HH:mm:ss}</green> | <cyan>{name}:{line}</cyan> | <level>{message}</level>"
        else:
            format_str = "<level>{level: <8}</level> | <green>{time:HH:mm:ss}</green> | <level>{message}</level>"

        logger.add(
            sys.stdout,
            format=format_str,
            level=self.log_level,
            colorize=True,
            diagnose=(self.log_level == "DEBUG"),
        )

    def add_tool(self, func: Callable[P, T]) -> Callable[P, T]:
        """
        Add a tool to the server.

        Directly add a tool to the server explicitly without
        a decorator. This will still require the tool to be annotated
        with a type and a description and use the docstring as the
        tool prompt.

        Example:
            ```python
            from arcade_mcp import MCPApp

            app = MCPApp(name="my_server", version="1.0.0")

            def greet(name: Annotated[str, "The name to greet"]) -> Annotated[str, "The greeting"]:
                return f"Hello, {name}!"

            app.add_tool(greet)

            app.run(host="127.0.0.1", port=7777)
            ```
        """
        # Wrap with tool decorator if not already wrapped
        if not hasattr(func, '__tool_name__'):
            func = tool_decorator(func)

        # Add to catalog with toolkit name
        self._catalog.add_tool(func, self._toolkit_name)
        logger.debug(f"Added tool: {func.__name__}")

        return func

    def tool(self, func: Callable[P, T]) -> Callable[P, T]:
        """Decorator to add a tool to the server.

        Tools must be decorated with @tool to be added to the server.
        They also need to have all parameters annotated with a type
        and a description.

        Example:
            ```python
            from arcade_mcp import MCPApp

            app = MCPApp(name="my_server", version="1.0.0")

            @app.tool
            def greet(name: Annotated[str, "The name to greet"]) -> Annotated[str, "The greeting"]:
                return f"Hello, {name}!"

            app.run(host="127.0.0.1", port=7777)
            ```

        To allow for both docstrings and function signatures, you can use the
        'desc' parameter to add a description to the tool which will override
        the docstring.

        ```python
        from arcade_mcp import MCPApp

        app = MCPApp(name="my_server", version="1.0.0")

        @app.tool(desc="Greet the user.")
        def greet(name: Annotated[str, "The name to greet"]) -> Annotated[str, "The greeting"]:
            '''Greet the user.

            Args:
                name: The name to greet

            Returns:
                The greeting
            '''
            return f"Hello, {name}!"
        ```
        """
        return self.add_tool(func)

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 7777,
        reload: bool = False,
        transport: TransportType = "http",
        **kwargs: Any,
    ) -> None:
        """
        Run an HTTP Streamable MCP Server

        Args:
            host: Host for transport
            port: Port for transport
            reload: Enable auto-reload for development
            transport: Transport type ("http" or "stdio")
            **kwargs: Additional transport-specific options
        """
        # Validate tools were added
        if len(self._catalog) == 0:
            logger.error("No tools added to the server. Use @app.tool decorator or app.add_tool().")
            sys.exit(1)

        logger.info(f"Starting {self.name} v{self.version} with {len(self._catalog)} tools")

        # Run the appropriate transport
        if transport in ["http", "streamable-http", "streamable"]:
            run_arcade_mcp(
                catalog=self._catalog,
                host=host,
                port=port,
                reload=reload,
                **self.server_kwargs,
            )
        elif transport == "stdio":
            from arcade_mcp.__main__ import run_stdio_server
            run_stdio_server(
                catalog=self._catalog,
                host=host,
                port=port,
                reload=reload,
                **self.server_kwargs,
            )
        else:
            raise TransportError(f"Invalid transport: {transport}")
