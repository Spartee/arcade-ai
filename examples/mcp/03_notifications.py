#!/usr/bin/env python3
"""03: Notifications via ToolContext


Clients that support notifications (Cursor/Claude Desktop/etc.) will render progress and log messages.
If unsupported, calls no-op gracefully. This example demonstrates:
- context.log.* → notifications/message
- context.notify.progress(...) → notifications/progress
- You can also mix in context.client.* calls in async tools
"""

import asyncio
from typing import Annotated

from arcade_mcp import Server, tool
from arcade_tdk import ToolContext


@tool(desc="Demonstrate progress and log notifications")
async def demo_progress(
    context: ToolContext,
    count: Annotated[int, "The number of steps to complete"] = 3,
) -> str:
    """Emit a few progress updates and log lines, then return a message."""
    await context.log.info("Starting demo")

    async with context.notify.progress(message="Working...", total=count) as tracker:
        for i in range(count):
            await asyncio.sleep(0.5)
            await tracker.update(i + 1, message=f"Step {i + 1} of {count}")
            if (i + 1) % 2 == 0:
                await context.log.debug(f"Reached step {i + 1}")

    await context.log.info("Done")
    return "Completed demo"


server = Server(name="notifications_example", version="0.1.0")
server.add_tool(demo_progress)


server.run(transport="streamable-http", host="127.0.0.1", port=8000)
