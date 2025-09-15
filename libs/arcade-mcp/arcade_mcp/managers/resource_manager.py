"""
Resource Manager

Manages resources in the MCP server with passive CRUD operations.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from arcade_mcp.exceptions import NotFoundError
from arcade_mcp.managers.base import ComponentManager
from arcade_mcp.types import Resource, ResourceTemplate, ResourceContents

logger = logging.getLogger("arcade.mcp.managers.resource")


class ResourceManager(ComponentManager[Resource]):
    """
    Manages resources for the MCP server.

    Passive manager: no per-manager locks or start/stop lifecycle.
    """

    def __init__(
        self,
        on_update=None,
    ):
        """
        Initialize resource manager.

        Args:
            on_update: Optional callback invoked when an existing resource/template is updated.
        """
        super().__init__("resource", on_update)
        self._templates: dict[str, ResourceTemplate] = {}
        self._resources: dict[str, Resource] = {}
        self._resource_handlers: dict[str, Callable[[str], Any]] = {}

    async def list_resources(self) -> list[Resource]:
        """
        List all available resources.

        Returns:
            List of resources
        """
        return list(self._resources.values())

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        """
        List all available resource templates.

        Returns:
            List of resource templates
        """
        return list(self._templates.values())

    async def read_resource(self, uri: str) -> list[ResourceContents]:
        """
        Read a resource by URI.

        Args:
            uri: Resource URI

        Returns:
            Resource contents

        Raises:
            NotFoundError: If resource not found
        """
        # Handler takes precedence when present
        if uri in self._resource_handlers:
            handler = self._resource_handlers[uri]
            result = handler(uri)
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[assignment]

            if isinstance(result, str):
                return [ResourceContents(uri=uri, text=result)]
            elif isinstance(result, dict):
                return [ResourceContents(uri=uri, **result)]
            elif isinstance(result, list):
                return result
            else:
                return [ResourceContents(uri=uri, text=str(result))]

        # Static resource must exist
        if uri not in self._resources:
            raise NotFoundError(f"Resource '{uri}' not found")

        # Default static content placeholder
        return [ResourceContents(uri=uri, text="")]

    async def add_resource(self, resource: Resource, handler: Callable[[str], Any] | None = None) -> None:
        """
        Add a resource to the manager.

        If a resource with the same URI exists, equality is checked. If equal, the
        call is a no-op. If different, the resource is replaced and on_update is
        invoked.

        Args:
        	resource: Resource to add
        	handler: Optional handler function to generate resource content
        """
        uri = resource.uri

        if uri in self._resources:
            existing = self._resources[uri]
            if existing == resource:
                return
            self._resources[uri] = resource
            if handler:
                self._resource_handlers[uri] = handler
            self._on_update(uri, existing, resource)
        else:
            self._resources[uri] = resource
            if handler:
                self._resource_handlers[uri] = handler

    async def remove_resource(self, uri: str) -> Resource:
        """
        Remove a resource from the manager.

        Args:
            uri: Resource URI

        Returns:
            The removed resource

        Raises:
            NotFoundError: If resource not found
        """
        if uri not in self._resources:
            raise NotFoundError(f"Resource '{uri}' not found")
        resource = self._resources.pop(uri)
        self._resource_handlers.pop(uri, None)
        return resource

    async def update_resource(self, uri: str, resource: Resource, handler: Callable[[str], Any] | None = None) -> Resource:
        """
        Update an existing resource.

        Args:
            uri: Current resource URI
            resource: New resource to replace it with
            handler: Optional new handler function

        Returns:
            The updated resource

        Raises:
            NotFoundError: If resource not found
        """
        if uri not in self._resources:
            raise NotFoundError(f"Resource '{uri}' not found")

        old_resource = self._resources.pop(uri)
        self._resource_handlers.pop(uri, None)

        self._resources[resource.uri] = resource
        if handler:
            self._resource_handlers[resource.uri] = handler

        self._on_update(resource.uri, old_resource, resource)
        return resource

    async def add_template(self, template: ResourceTemplate) -> None:
        """
        Add a resource template.

        If a template with the same uriTemplate exists and differs, it is replaced
        and on_update is invoked.

        Args:
            template: Template to add
        """
        uri_template = template.uriTemplate
        if uri_template in self._templates:
            existing = self._templates[uri_template]
            if existing == template:
                return
            self._templates[uri_template] = template
            self._on_update(uri_template, existing, template)
        else:
            self._templates[uri_template] = template

    async def remove_template(self, uri_template: str) -> ResourceTemplate:
        """
        Remove a resource template.

        Args:
            uri_template: Template URI template

        Returns:
            The removed template

        Raises:
            NotFoundError: If template not found
        """
        if uri_template not in self._templates:
            raise NotFoundError(f"Resource template '{uri_template}' not found")
        return self._templates.pop(uri_template)