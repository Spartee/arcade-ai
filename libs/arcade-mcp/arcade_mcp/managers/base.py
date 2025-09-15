"""
Base Manager Class

Provides common functionality for component managers (tools, resources, prompts).
"""

import logging
from typing import Any, Callable, Generic, Iterator, Protocol, TypeVar

from arcade_mcp.exceptions import NotFoundError

logger = logging.getLogger("arcade.mcp.managers")


class ComponentProtocol(Protocol):
    """Protocol that all managed components must implement.

    Components must define equality so managers can determine whether
    an incoming registration represents a no-op or an update.
    """

    def __eq__(self, other: object) -> bool:  # pragma: no cover - protocol definition
        ...


T = TypeVar("T", bound=ComponentProtocol)


class ComponentManager(Generic[T]):
    """
    Base class for managing MCP components (tools, resources, prompts).

    Provides common functionality for:
    - Component registration
    - Update notifications via callback
    - Component lookup
    """

    def __init__(
        self,
        component_type: str,
        on_update: Callable[[str, T, T], None] | None = None,
    ):
        """
        Initialize component manager.

        Args:
            component_type: Type of component (e.g., "tool", "resource", "prompt")
            on_update: Optional callback invoked when an existing component is updated.
                Signature: (key, old_component, new_component) -> None
        """
        self.component_type = component_type
        self._components: dict[str, T] = {}
        # Default callback logs an info-level message
        self._on_update: Callable[[str, T, T], None] = (
            on_update
            if on_update is not None
            else lambda key, old, new: logger.info(
                f"{self.component_type.title()} '{key}' updated"
            )
        )

    def add(self, key: str, component: T) -> None:
        """
        Add a component to the manager. If a component with the same key exists,
        it will be compared using component equality. If equal, the operation is
        a no-op. If not equal, the component will be replaced and the update
        callback will be invoked.

        Args:
            key: Unique key for the component
            component: Component to add
        """
        if key in self._components:
            existing = self._components[key]
            if existing == component:
                return
            self._components[key] = component
            self._on_update(key, existing, component)
        else:
            self._components[key] = component

    def remove(self, key: str) -> T:
        """
        Remove a component from the manager.

        Args:
            key: Component key

        Returns:
            The removed component

        Raises:
            NotFoundError: If component not found
        """
        if key not in self._components:
            raise NotFoundError(f"{self.component_type.title()} '{key}' not found")
        return self._components.pop(key)

    def get(self, key: str) -> T:
        """
        Get a component by key.

        Args:
            key: Component key

        Returns:
            The component

        Raises:
            NotFoundError: If component not found
        """
        if key not in self._components:
            raise NotFoundError(f"{self.component_type.title()} '{key}' not found")
        return self._components[key]

    def has(self, key: str) -> bool:
        """Check if a component exists."""
        return key in self._components

    def list_keys(self) -> list[str]:
        """List all component keys."""
        return list(self._components.keys())

    def list_components(self) -> list[T]:
        """List all components."""
        return list(self._components.values())

    def clear(self) -> None:
        """Clear all components."""
        self._components.clear()

    def __eq__(self, other: Any) -> bool:
        """Check if two managers are equal."""
        return isinstance(other, ComponentManager) and self._components == other._components

    def __len__(self) -> int:
        """Get the number of components."""
        return len(self._components)

    def __iter__(self) -> Iterator[T]:
        """Iterate over components."""
        return iter(self._components.values())