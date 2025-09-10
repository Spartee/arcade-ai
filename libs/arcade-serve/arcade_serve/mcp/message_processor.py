import inspect
import json
import logging
from typing import Any, Callable, TypeVar

from arcade_serve.mcp.types import JSONRPCRequest, MCPMessage

logger = logging.getLogger("arcade.mcp")

T = TypeVar("T")

# Type definition for middleware functions
MessageProcessor = Callable[[Any, str], Any]


class MCPMessageProcessor:
    """
    Processes MCP messages through a chain of middleware.
    Supports both synchronous and asynchronous middleware.
    """

    def __init__(self) -> None:
        self.middleware: list[Callable[[MCPMessage, str], Any]] = []

    def add_middleware(self, mw: Callable[[MCPMessage, str], Any]) -> None:
        self.middleware.append(mw)

    async def process(self, message: Any, direction: str) -> Any:  # noqa: C901
        # First, try to parse the message if it's a string
        if isinstance(message, str):
            # Strip any whitespace including newlines
            message = message.strip()
            if not message:
                return None

            try:
                parsed = json.loads(message)
                if isinstance(parsed, dict):
                    method = parsed.get("method")
                    # Convert to appropriate message type
                    if method and method.startswith("notifications/"):
                        # It's a notification, log it but pass through as dict
                        logger.debug(f"Received notification: {method}")
                        message = parsed
                    elif "method" in parsed and "id" in parsed:
                        # Regular method request (generic JSON-RPC request)
                        logger.debug(f"Parsed method request: {method}")
                        message = JSONRPCRequest(**parsed)
                    # Other message types can be handled similarly
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse message as JSON: {message[:100]}...")
            except Exception:
                logger.exception("Error processing message")

        # Process through middleware chain
        result = message
        for mw in self.middleware:
            try:
                if inspect.iscoroutinefunction(mw):
                    result = await mw(result, direction)
                else:
                    result = mw(result, direction)
            except Exception:
                logger.exception(f"Error in middleware {mw}")
        return result

    async def process_request(self, message: Any) -> Any:
        return await self.process(message, "request")

    async def process_response(self, message: Any) -> Any:
        return await self.process(message, "response")


def create_message_processor(*middleware: MessageProcessor) -> MCPMessageProcessor:
    processor = MCPMessageProcessor()
    for m in middleware:
        if m is not None:
            processor.add_middleware(m)
    return processor
