#!/usr/bin/env python3
"""04: Read secrets from .env via ToolContext

Run:
  # Create a .env in the working directory with:
  #   API_KEY=supersecret
  # Then start the server:

"""

from arcade_mcp import Server
from arcade_tdk import ToolContext, tool


@tool(
    name="UseSecret",
    desc="Echo a masked secret read from the context",
    requires_secrets=["API_KEY"],  # declare we need API_KEY
)
def use_secret(context: ToolContext) -> str:
    """Read API_KEY from context and return a masked confirmation string."""
    try:
        value = context.get_secret("API_KEY")
        masked = value[:2] + "***" if len(value) >= 2 else "***"
        return f"Got API_KEY of length {len(value)} -> {masked}"
    except Exception as e:
        return f"Error getting secret: {e}"


server = Server(name="secrets_example", version="0.1.0")
server.add_tool(use_secret)


server.run(transport="streamable-http", host="127.0.0.1", port=8000)
