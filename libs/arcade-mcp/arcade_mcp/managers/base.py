"""
Base Registry Class

Provides common functionality for registry managers (tools, resources, prompts).
"""

import logging
from collections.abc import Iterator
from typing import Any, Generic, TypeVar

from arcade_mcp.exceptions import NotFoundError

logger = logging.getLogger("arcade.mcp.managers")

# Type variable for registry items - no protocol needed, subclasses define their own types
T = TypeVar("T")


class Registry(Generic[T]):
    """
    Base container class for managing MCP registries (tools, resources, prompts).
    """

    def __init__(
        self,
        component: str,
    ):
        """
        Initialize registry manager.

        Args:
            component: Type of registry (e.g., "tool", "resource", "prompt")
        """
        self.component = component
        self._registries: dict[str, T] = {}

    def add(self, key: str, registry: T) -> None:
        """
        Add a registry to the manager.
        Overwrites existing registry if it exists.
        """
        self._registries[key] = registry

    def update(self, registries: dict[str, T]) -> None:
        """
        Update multiple registries in the manager.
        Overwrites existing registries if they exist.
        """
        for key, registry in registries.items():
            self._registries[key] = registry

    def remove(self, key: str) -> T:
        """
        Remove a registry from the manager.
        """
        if key not in self._registries:
            raise NotFoundError(f"{self.component.title()} '{key}' not found")
        return self._registries.pop(key)

    def get(self, key: str) -> T:
        """
        Get a registry from the manager.
        """
        if key not in self._registries:
            raise NotFoundError(f"{self.component.title()} '{key}' not found")
        return self._registries[key]

    def keys(self) -> list[str]:
        """List all registry keys."""
        return list(self._registries.keys())

    def __iter__(self) -> Iterator[T]:
        """Iterate over registries."""
        return iter(self._registries.values())

    def clear(self) -> None:
        """Clear all registries."""
        self._registries.clear()

    def __eq__(self, other: Any) -> bool:
        """Check if two managers are equal."""
        return isinstance(other, Registry) and self._registries == other._registries

    def __len__(self) -> int:
        """Get the number of registries."""
        return len(self._registries)

    def __contains__(self, key: str) -> bool:
        """Check if a registry exists in the manager."""
        return key in self._registries
