#!/usr/bin/env python3
"""This example show how to create a simple MCP server with a single tool.

- Define a tool with @tool
- Start an MCP server using arcade_mcp.Server
- Run with any transport: stream (default), sse, stdio.
"""

from typing import Annotated

from arcade_mcp import Server
from arcade_tdk import tool


@tool(desc="Echo back the provided text")
def echo(text: Annotated[str, "The text to echo back"]) -> str:
    """Return the same text that was provided.

    Minimal example of a tool defined in the same file as the server.
    """
    return text


# Create server and register the local tool
server = Server(name="simple_mcp", version="0.1.0", log_level="DEBUG")
server.add_tool(echo)
server.run(
    transport="streamable-http",  # or "stdio"
    host="127.0.0.1",
    port=8000,
)
