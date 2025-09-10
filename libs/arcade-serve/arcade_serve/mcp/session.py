import asyncio
from enum import Enum
from typing import Any, AsyncIterator

from arcade_serve.mcp.types import (
    ClientCapabilities,
    InitializeParams,
)


class InitializationState(Enum):
    NOT_INITIALIZED = 1
    INITIALIZING = 2
    INITIALIZED = 3


class ServerSession:
    """
    Centralized MCP server session that tracks initialization state and client params
    and provides helpers for capability checks.
    """

    def __init__(
        self,
        server: Any,
        user_id: str,
        read_stream: AsyncIterator[str] | None = None,
        write_stream: Any | None = None,
        init_options: Any | None = None,
        stateless: bool = False,
    ) -> None:
        self.server = server
        self.user_id = user_id
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.init_options = init_options
        self.initialization_state: InitializationState = (
            InitializationState.INITIALIZED if stateless else InitializationState.NOT_INITIALIZED
        )
        self.client_params: InitializeParams | None = None
        # Optional request manager for server→client requests (set by server)
        self._request_manager: Any | None = None

    async def run(self) -> None:
        """Run the session receive loop. Only used in stream-oriented transports."""
        if self.read_stream is None or self.write_stream is None:
            return

        async for message in self.read_stream:
            response = await self.server.handle_message(message, user_id=self.user_id, session=self)
            if response is None:
                continue
            await self.server._send_response(self.write_stream, response)

    def set_client_params(self, params: InitializeParams) -> None:
        self.client_params = params
        self.initialization_state = InitializationState.INITIALIZING

    def mark_initialized(self) -> None:
        self.initialization_state = InitializationState.INITIALIZED

    def check_client_capability(self, capability: ClientCapabilities) -> bool:
        """Check if the client declared the provided capability set during initialize."""
        if self.client_params is None:
            return False
        client_caps = self.client_params.capabilities

        # Roots
        if capability.roots is not None:
            if client_caps.roots is None:
                return False
            cap_list_changed = capability.roots.get("listChanged") if isinstance(capability.roots, dict) else getattr(capability.roots, "listChanged", None)
            cli_list_changed = client_caps.roots.get("listChanged") if isinstance(client_caps.roots, dict) else getattr(client_caps.roots, "listChanged", None)
            if cap_list_changed and not cli_list_changed:
                return False

        # Sampling
        if capability.sampling is not None and client_caps.sampling is None:
            return False

        # Elicitation
        if capability.elicitation is not None and client_caps.elicitation is None:
            return False

        # Experimental
        if capability.experimental is not None:
            if client_caps.experimental is None:
                return False
            for key, value in capability.experimental.items():
                if key not in client_caps.experimental:
                    return False
                if client_caps.experimental[key] != value:
                    return False

        return True