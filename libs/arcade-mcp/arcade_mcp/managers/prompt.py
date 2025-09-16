"""
Prompt Manager

Manages prompts in the MCP server with passive CRUD operations.
"""

from __future__ import annotations

import logging
from typing import Callable

from arcade_mcp.exceptions import NotFoundError, PromptError
from arcade_mcp.managers.base import Registry
from arcade_mcp.types import GetPromptResult, Prompt, PromptMessage

logger = logging.getLogger("arcade.mcp.managers.prompt")


class PromptHandler:
    """Handler for generating prompt messages."""

    def __init__(
        self,
        prompt: Prompt,
        handler: Callable[[dict[str, str]], list[PromptMessage]] | None = None,
    ) -> None:
        """
        Initialize prompt handler.

        Args:
            prompt: The prompt definition
            handler: Optional function to generate messages
        """
        self.prompt = prompt
        self.handler = handler or self._default_handler

    def __eq__(self, other: object) -> bool:  # pragma: no cover - simple comparison
        if not isinstance(other, PromptHandler):
            return False
        return self.prompt == other.prompt and self.handler == other.handler

    def _default_handler(self, arguments: dict[str, str]) -> list[PromptMessage]:
        """Default handler that returns prompt description as a message."""
        return [
            PromptMessage(
                role="user",
                content={
                    "type": "text",
                    "text": self.prompt.description or f"Prompt: {self.prompt.name}",
                },
            )
        ]

    async def get_messages(self, arguments: dict[str, str] | None = None) -> list[PromptMessage]:
        """
        Get prompt messages with given arguments.

        Args:
            arguments: Arguments for the prompt

        Returns:
            List of prompt messages
        """
        args = arguments or {}

        # Validate required arguments
        if self.prompt.arguments:
            for arg in self.prompt.arguments:
                if arg.required and arg.name not in args:
                    raise PromptError(f"Required argument '{arg.name}' not provided")

        result = self.handler(args)
        if hasattr(result, "__await__"):
            result = await result

        return result


class PromptManager(Registry[PromptHandler]):
    """
    Manages prompts for the MCP server.
    """

    def __init__(
        self,
    ) -> None:
        """
        Initialize prompt manager.
        """
        super().__init__("prompt")

    async def list_prompts(self) -> list[Prompt]:
        """
        List all available prompts.

        Returns:
            List of prompts
        """
        return [handler.prompt for handler in self]

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        """
        Get a prompt by name.

        Args:
            name: Prompt name
            arguments: Optional arguments for the prompt

        Returns:
            Prompt result

        Raises:
            NotFoundError: If prompt not found
        """
        handler = self.get(name)

        try:
            messages = await handler.get_messages(arguments)
            return GetPromptResult(
                description=handler.prompt.description,
                messages=messages,
            )
        except Exception as e:
            if isinstance(e, PromptError):
                raise
            raise PromptError(f"Error generating prompt: {e}") from e

    async def add_prompt(
        self,
        prompt: Prompt,
        handler: Callable[[dict[str, str]], list[PromptMessage]] | None = None,
    ) -> None:
        """
        Add a prompt to the manager.

        Args:
            prompt: Prompt to add
            handler: Optional handler function to generate messages
        """
        prompt_handler = PromptHandler(prompt, handler)

        self.add(prompt.name, prompt_handler)

    async def remove_prompt(self, name: str) -> Prompt:
        """
        Remove a prompt from the manager.

        Args:
            name: Prompt name

        Returns:
            The removed prompt

        Raises:
            NotFoundError: If prompt not found
        """
        handler = self.remove(name)
        return handler.prompt

    async def update_prompt(
        self,
        name: str,
        prompt: Prompt,
        handler: Callable[[dict[str, str]], list[PromptMessage]] | None = None,
    ) -> Prompt:
        """
        Update an existing prompt.

        Args:
            name: Current prompt name
            prompt: New prompt to replace it with
            handler: Optional new handler function

        Returns:
            The updated prompt

        Raises:
            NotFoundError: If prompt not found
        """
        # Verify the prompt exists
        self.get(name)
        
        # Create new handler and update
        prompt_handler = PromptHandler(prompt, handler)
        self.add(prompt.name, prompt_handler)

        return prompt
