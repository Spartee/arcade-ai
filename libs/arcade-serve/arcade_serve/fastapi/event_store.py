from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable

EventCallback = Callable[[str, dict[str, Any]], asyncio.Future | Any]


class EventStore(ABC):
    """
    Interface for resumability support via event storage.
    Stores server→client JSON-RPC messages per stream with ordered event IDs.
    """

    @abstractmethod
    async def create_stream(self) -> str:
        """Create and return a new stream_id."""

    @abstractmethod
    async def store_event(self, stream_id: str, message: dict[str, Any]) -> str:
        """Append a message to the stream and return the assigned event_id."""

    @abstractmethod
    async def replay_events_after(
        self,
        stream_id: str,
        last_event_id: str | None,
        limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """
        Return (event_id, message) for events with id > last_event_id up to limit.
        """

    @abstractmethod
    async def get_tail_id(self, stream_id: str) -> str | None:
        """Return the highest event id (tail) for the stream, if any."""

    @abstractmethod
    async def delete_stream(self, stream_id: str) -> None:
        """Delete the stream and all stored events."""


class InMemoryEventStore(EventStore):
    def __init__(self, max_events_per_stream: int = 1000) -> None:
        self._events: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        self._counters: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._max = max_events_per_stream

    async def create_stream(self) -> str:
        # Caller typically already has session_id; just return a token if needed
        # Here we rely on caller-provided session IDs, so no-op
        # Return empty string to indicate not used
        return ""

    async def store_event(self, stream_id: str, message: dict[str, Any]) -> str:
        async with self._lock:
            ctr = self._counters.get(stream_id, 0) + 1
            self._counters[stream_id] = ctr
            stream = self._events.setdefault(stream_id, [])
            stream.append((ctr, message))
            # Trim oldest if beyond max
            if len(stream) > self._max:
                del stream[: len(stream) - self._max]
            return str(ctr)

    async def replay_events_after(
        self,
        stream_id: str,
        last_event_id: str | None,
        limit: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        async with self._lock:
            stream = self._events.get(stream_id, [])
            start_idx = 0
            if last_event_id is not None:
                try:
                    last_id_int = int(last_event_id)
                except ValueError:
                    last_id_int = -1
                # find first with id > last_event_id
                for i, (eid, _) in enumerate(stream):
                    if eid > last_id_int:
                        start_idx = i
                        break
                else:
                    return []
            items = stream[start_idx:]
            if limit is not None:
                items = items[:limit]
            return [(str(eid), msg) for eid, msg in items]

    async def get_tail_id(self, stream_id: str) -> str | None:
        async with self._lock:
            stream = self._events.get(stream_id, [])
            if not stream:
                return None
            return str(stream[-1][0])

    async def delete_stream(self, stream_id: str) -> None:
        async with self._lock:
            self._events.pop(stream_id, None)
            self._counters.pop(stream_id, None)