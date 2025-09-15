"""
Base Components

Provides base classes and common functionality for MCP components.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from arcade_mcp.exceptions import MCPError

T = TypeVar("T")


class MCPComponent(ABC):
    """
    Base class for all MCP components.

    Provides common functionality like:
    - Logging setup
    - Lifecycle management
    - Error handling
    - Component metadata
    """

    def __init__(self, name: str | None = None):
        """
        Initialize component.

        Args:
            name: Component name (defaults to class name)
        """
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(f"arcade.mcp.{self.name.lower()}")
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """
        Start the component.

        This method is idempotent - calling it multiple times is safe.
        """
        async with self._lock:
            if self._started:
                self.logger.debug(f"{self.name} already started")
                return

            self.logger.info(f"Starting {self.name}")
            try:
                await self._start()
                self._started = True
                self.logger.info(f"{self.name} started successfully")
            except Exception as e:
                self.logger.error(f"Failed to start {self.name}: {e}")
                raise MCPError(f"Failed to start {self.name}: {e}") from e

    async def stop(self) -> None:
        """
        Stop the component.

        This method is idempotent - calling it multiple times is safe.
        """
        async with self._lock:
            if not self._started:
                self.logger.debug(f"{self.name} not started")
                return

            self.logger.info(f"Stopping {self.name}")
            try:
                await self._stop()
                self._started = False
                self.logger.info(f"{self.name} stopped successfully")
            except Exception as e:
                self.logger.error(f"Failed to stop {self.name}: {e}")
                # Don't raise on stop errors - best effort

    @abstractmethod
    async def _start(self) -> None:
        """
        Component-specific start logic.

        Override this method to implement startup behavior.
        """
        pass

    @abstractmethod
    async def _stop(self) -> None:
        """
        Component-specific stop logic.

        Override this method to implement shutdown behavior.
        """
        pass

    @property
    def is_started(self) -> bool:
        """Check if component is started."""
        return self._started

    def __repr__(self) -> str:
        """String representation."""
        return f"<{self.name} started={self._started}>"
