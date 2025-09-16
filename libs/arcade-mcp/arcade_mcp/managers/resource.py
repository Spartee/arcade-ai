"""
Resource Manager

Manages resources in the MCP server with passive CRUD operations.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from arcade_mcp.exceptions import NotFoundError
from arcade_mcp.managers.base import Registry
from arcade_mcp.types import (
    BlobResourceContents,
    Resource,
    ResourceContents,
    ResourceTemplate,
    TextResourceContents,
)

logger = logging.getLogger("arcade.mcp.managers.resource")


class ResourceManager(Registry[Resource]):
    """
    Manages resources for the MCP server.
    """

    def __init__(
        self,
    ) -> None:
        """
        Initialize resource manager.
        """
        super().__init__("resource")
        # Additional storage for templates and handlers
        self._templates: dict[str, ResourceTemplate] = {}
        self._resource_handlers: dict[str, Callable[[str], Any]] = {}

    async def list_resources(self) -> list[Resource]:
        """
        List all available resources.

        Returns:
            List of resources
        """
        return list(self)

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
                result = await result

            if isinstance(result, str):
                return [TextResourceContents(uri=uri, text=result)]
            elif isinstance(result, dict):
                # Accept either text or blob payloads
                if "text" in result:
                    return [TextResourceContents(uri=uri, text=result["text"])]
                if "blob" in result:
                    return [BlobResourceContents(uri=uri, blob=result["blob"])]
                return [ResourceContents(uri=uri)]
            elif isinstance(result, list):
                return result
            else:
                return [TextResourceContents(uri=uri, text=str(result))]

        # Static resource must exist
        if uri not in self:
            raise NotFoundError(f"Resource '{uri}' not found")

        # Default static content placeholder
        return [TextResourceContents(uri=uri, text="")]

    async def add_resource(
        self, resource: Resource, handler: Callable[[str], Any] | None = None
    ) -> None:
        """
        Add a resource to the manager.
        Args:
                resource: Resource to add
                handler: Optional handler function to generate resource content
        """
        uri = resource.uri
        self.add(uri, resource)
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
        resource = self.remove(uri)
        self._resource_handlers.pop(uri, None)
        return resource

    async def update_resource(
        self, uri: str, resource: Resource, handler: Callable[[str], Any] | None = None
    ) -> Resource:
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
        # Remove old resource
        self.remove(uri)
        self._resource_handlers.pop(uri, None)

        # Add new resource
        self.add(resource.uri, resource)
        if handler:
            self._resource_handlers[resource.uri] = handler

        return resource

    async def add_template(self, template: ResourceTemplate) -> None:
        """
        Add a resource template.

        Args:
            template: Template to add
        """
        uri_template = template.uriTemplate
        if uri_template in self._templates:
            self._templates[uri_template] = template
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
        removed = self._templates.pop(uri_template)
        return removed
