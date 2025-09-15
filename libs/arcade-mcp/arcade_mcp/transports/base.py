"""
Base Transport Layer

Provides abstract base class for MCP transport implementations following
the pattern from the sample library.
"""

import abc
import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from arcade_mcp.exceptions import TransportError


class WriteStream(Protocol):
    """Protocol for write streams."""
    async def send(self, data: str) -> None:
        """Send data to the stream."""
        ...


class ReadStream(Protocol):
    """Protocol for read streams."""
    def __aiter__(self) -> AsyncIterator[str]:
        """Iterate over incoming messages."""
        ...


@dataclass
class TransportSession:
    """
    Represents an active transport session.

    This encapsulates the read/write streams and session metadata
    for a single MCP connection.
    """
    read_stream: ReadStream
    write_stream: WriteStream
    session_id: str
    user_id: str | None = None
    init_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate session after initialization."""
        if not self.session_id:
            raise ValueError("session_id is required")


class Transport(abc.ABC):
    """
    Abstract base class for MCP transport mechanisms.

    A Transport is responsible for establishing and managing connections
    to MCP clients, providing a TransportSession within an async context.

    Following the sample library pattern, transports use context managers
    for proper resource lifecycle management.
    """

    def __init__(self, name: str | None = None):
        """
        Initialize transport.

        Args:
            name: Optional transport name for logging/debugging
        """
        self.name = name or self.__class__.__name__
        self._sessions: dict[str, TransportSession] = {}
        self._lock = asyncio.Lock()

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        user_id: str | None = None,
        **options: Any
    ) -> AsyncIterator[TransportSession]:
        """
        Establishes a connection and yields an active TransportSession.

        The session is guaranteed to be valid only within the scope of the
        async context manager. Connection setup and teardown are handled
        within this context.

        Args:
            user_id: Optional user identifier
            **options: Transport-specific options

        Yields:
            A TransportSession instance
        """
        raise NotImplementedError
        yield  # type: ignore

    @abc.abstractmethod
    async def start(self) -> None:
        """
        Start the transport.

        This is called once when the server starts up.
        """
        pass

    @abc.abstractmethod
    async def stop(self) -> None:
        """
        Stop the transport.

        This is called once when the server shuts down.
        Should clean up all resources and close all connections.
        """
        pass

    async def register_session(self, session: TransportSession) -> None:
        """Register an active session."""
        async with self._lock:
            self._sessions[session.session_id] = session

    async def unregister_session(self, session_id: str) -> None:
        """Unregister a session."""
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def get_session(self, session_id: str) -> TransportSession | None:
        """Get a session by ID."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        async with self._lock:
            return list(self._sessions.keys())

    def __repr__(self) -> str:
        """String representation."""
        return f"<{self.name}>"