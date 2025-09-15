"""
Stdio Transport

Provides stdio (stdin/stdout) transport for MCP communication.
"""

import asyncio
import contextlib
import logging
import queue
import signal
import sys
import threading
import uuid
from collections.abc import AsyncIterator
from typing import Any

from arcade_mcp.exceptions import TransportError
from arcade_mcp.transports.base import Transport, TransportSession

logger = logging.getLogger("arcade.mcp.transports.stdio")


class StdioWriteStream:
    """Write stream implementation for stdio."""

    def __init__(self, write_queue: queue.Queue[str | None]):
        self.write_queue = write_queue

    async def send(self, data: str) -> None:
        """Send data to stdout."""
        if not data.endswith("\n"):
            data += "\n"
        await asyncio.to_thread(self.write_queue.put, data)


class StdioReadStream:
    """Read stream implementation for stdio."""

    def __init__(self, read_queue: queue.Queue[str | None]):
        self.read_queue = read_queue
        self._running = True

    def stop(self) -> None:
        """Stop the read stream."""
        self._running = False

    async def __aiter__(self) -> AsyncIterator[str]:
        """Async iteration over incoming messages."""
        while self._running:
            try:
                line = await asyncio.to_thread(self.read_queue.get)
                if line is None:
                    break
                yield line
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error reading from stdin")
                raise TransportError(f"Read error: {e}") from e


class StdioTransport(Transport):
    """
    Transport implementation for stdio communication.

    This transport uses stdin/stdout for MCP communication,
    suitable for command-line tools and scripts.
    """

    def __init__(self, name: str = "stdio"):
        """Initialize stdio transport."""
        super().__init__(name)
        self.read_queue: queue.Queue[str | None] = queue.Queue()
        self.write_queue: queue.Queue[str | None] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.writer_thread: threading.Thread | None = None
        self._shutdown_event = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        """Start the transport."""
        await super().start()

        # Start I/O threads
        self._running = True
        self.reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name=f"{self.name}-reader",
        )
        self.writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name=f"{self.name}-writer",
        )
        self.reader_thread.start()
        self.writer_thread.start()

        # Set up signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self.stop())
                )
            except NotImplementedError:
                # Windows doesn't support POSIX signals
                if sys.platform == "win32":
                    logger.warning("Signal handling not fully supported on Windows")
                else:
                    logger.warning(f"Failed to set up signal handler for {sig}")

    async def stop(self) -> None:
        """Stop the transport."""
        if not self._running:
            return

        logger.info("Stopping stdio transport")
        self._running = False

        # Signal threads to stop
        self.read_queue.put(None)
        self.write_queue.put(None)

        # Wait for threads to finish
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=1.0)

        # Set shutdown event
        self._shutdown_event.set()

        await super().stop()

    def _reader_loop(self) -> None:
        """Reader thread loop."""
        try:
            for line in sys.stdin:
                if not self._running:
                    break
                self.read_queue.put(line.strip())
        except Exception:
            logger.exception("Error in reader thread")
        finally:
            self.read_queue.put(None)  # Signal EOF

    def _writer_loop(self) -> None:
        """Writer thread loop."""
        try:
            while self._running:
                msg = self.write_queue.get()
                if msg is None:
                    break
                sys.stdout.write(msg)
                sys.stdout.flush()
        except Exception:
            logger.exception("Error in writer thread")

    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        user_id: str | None = None,
        **options: Any
    ) -> AsyncIterator[TransportSession]:
        """
        Create a stdio session.

        Since stdio is inherently single-session, this will fail
        if a session is already active.
        """
        # Check if already have a session
        sessions = await self.list_sessions()
        if sessions:
            raise TransportError("Stdio transport only supports one session")

        # Create session
        session_id = str(uuid.uuid4())
        read_stream = StdioReadStream(self.read_queue)
        write_stream = StdioWriteStream(self.write_queue)

        session = TransportSession(
            read_stream=read_stream,
            write_stream=write_stream,
            session_id=session_id,
            user_id=user_id,
            init_options=options,
        )

        # Register session
        await self.register_session(session)

        try:
            yield session
        finally:
            # Cleanup
            read_stream.stop()
            await self.unregister_session(session_id)

    async def wait_for_shutdown(self) -> None:
        """Wait for the transport to shut down."""
        await self._shutdown_event.wait()

    async def _start(self) -> None:
        """Component-specific start logic."""
        # Already handled in start()
        pass

    async def _stop(self) -> None:
        """Component-specific stop logic."""
        # Already handled in stop()
        pass