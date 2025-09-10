#!/usr/bin/env python3
"""02: Add a Toolkit by importing and registering it on the server (spec 2025-06-18).

This example demonstrates loading an installed toolkit and serving tools via MCP:
- Load a `Toolkit` from a package (auto-discovers @tool functions)
- Register toolkit on `arcade_mcp.Server`
- Run with stream/sse/stdio transports

All tools receive a ToolContext with:
- logging (context.log.*) → notifications/message
- progress (context.notify.progress) → notifications/progress
- secrets/auth applied automatically when declared
- client-side MCP APIs: context.client.create_message / list_roots / elicit / complete
"""

from arcade_core import Toolkit
from arcade_mcp import Server

toolkit = Toolkit.from_package("arcade_math")

# Create server and add the toolkit
server = Server(name="add_toolkit_example", version="0.1.0")
server.add_toolkit(toolkit)

server.run(transport="streamable-http", host="127.0.0.1", port=8000)
