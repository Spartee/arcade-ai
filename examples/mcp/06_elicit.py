#!/usr/bin/env python3
"""06: Elicitation from tools using context.client.elicit

Run:
  python 06_elicit.py
"""
from typing import Annotated

from arcade_core.schema import ToolContext
from arcade_mcp import Server, tool


@tool(desc="Ask the user for their email via elicitation")
async def ask_email(context: ToolContext, prompt: Annotated[str, "Prompt to show"] = "Enter your email") -> str:
    # Primitive-only schema per spec
    requested_schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email", "title": "Email"},
        },
        "required": ["email"],
    }

    result = await context.client.elicit(message=prompt, requested_schema=requested_schema)

    action = result.get("action")
    if action == "accept":
        content = result.get("content", {}) or {}
        return f"Thanks! We received: {content.get('email', '')}"
    elif action == "decline":
        return "User declined to provide input"
    elif action == "cancel":
        return "User canceled elicitation"
    else:
        return f"Unexpected action: {action}"


server = Server(name="elicitation_example", version="0.1.0")
server.add_tool(ask_email)


server.run(transport="streamable-http", host="127.0.0.1", port=8000)