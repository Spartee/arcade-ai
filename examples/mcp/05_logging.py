#!/usr/bin/env python3
"""05: Logging from tools using ToolContext

Demonstrates sending notifications/message at various levels.
Works on all transports; clients may filter based on setLevel.

Run:
  python 05_logging.py
"""

from typing import Annotated

from arcade_mcp import Server
from arcade_tdk import ToolContext, tool


@tool(desc="Emit logs at multiple levels")
async def demo_logging(context: ToolContext, message: Annotated[str, "Base message"] = "Hello") -> str:
    await context.log.debug(f"debug: {message}")
    await context.log.info(f"info: {message}")
    await context.log.notice(f"notice: {message}")
    await context.log.warning(f"warning: {message}")
    await context.log.error(f"error: {message}")
    await context.log.critical(f"critical: {message}")
    return "Logged at all levels"


server = Server(name="logging_example", version="0.1.0", enable_logging=True)
server.add_tool(demo_logging)


server.run(transport="streamable-http", host="127.0.0.1", port=8000)