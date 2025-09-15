#!/usr/bin/env python3
"""MCP server for {{ toolkit_name }} toolkit."""

import sys
from arcade_mcp import Server
from {{ package_name }}.tools import *

# Create the server
server = Server(
    name="{{ toolkit_name }}",
    version="0.1.0",
)

# The server automatically discovers tools from the imports above
# You can also explicitly add tools or toolkits:
# server.add_tool(my_tool)
# server.add_toolkit(my_toolkit)

if __name__ == "__main__":
    # Get transport from command line argument, default to "streamable-http"
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"

    # Run the server
    # - "streamable-http" (default): JSON per request; compatible with MCP Inspector
    # - "sse": Server-sent events for legacy clients (bi-directional pattern)
    # - "stdio": Standard I/O for VS Code and CLI tools
    server.run(transport=transport, host="0.0.0.0", port=7777)