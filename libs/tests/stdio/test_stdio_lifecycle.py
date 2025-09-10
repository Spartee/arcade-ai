import asyncio
import types
import pytest

from arcade_core.catalog import ToolCatalog
from arcade_serve.mcp.stdio import StdioServer


@pytest.mark.asyncio
async def test_stdio_server_start_stop() -> None:
    catalog = ToolCatalog()
    server = StdioServer(catalog=catalog, auth_disabled=True)

    async def run_and_stop() -> None:
        # start in background and cancel shortly after
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.1)
        await server.shutdown()
        # wait for graceful shutdown
        await asyncio.sleep(0.1)
        # cancel the run task if still pending
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    await run_and_stop()