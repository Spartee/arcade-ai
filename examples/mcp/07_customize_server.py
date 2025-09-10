#!/usr/bin/env python3
"""07: Customize server using arcade-serve directly (spec 2025-06-18).

Demonstrates integrating MCPServer into a FastAPI app manually, using StreamableHTTPComponent.
This gives full control over mounting, middleware, and other app settings.

Run (example):
  python 07_customize_server.py
"""

from typing import Annotated

from fastapi import FastAPI
import uvicorn

from arcade_mcp import Server, tool
from arcade_serve.fastapi.streamable import StreamableHTTPComponent
from arcade_serve.fastapi.worker import FastAPIWorker


@tool(desc="Return a friendly greeting")
def hello(name: Annotated[str, "Name to greet"]) -> str:
    return f"Hello, {name}!"


# -----------------------------
# Pass in a FastAPI app
# -----------------------------

app = FastAPI(title="Custom Arcade MCP Server")
server = Server(app)
server.add_tool(hello)
server.run(transport="streamable-http", host="127.0.0.1", port=8000)






# -----------------------------
# Add to existing FastAPI app
# -----------------------------

# Create a FastAPI app and run with streamable-http transport
app = FastAPI(title="Custom Arcade MCP Server")

worker = FastAPIWorker(app)

worker.register_component(StreamableHTTPComponent)
worker.catalog.add_tool(hello, "LocalToolkit")

uvicorn.run(app, host="0.0.0.0", port=8000)