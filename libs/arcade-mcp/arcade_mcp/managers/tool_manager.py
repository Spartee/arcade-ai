"""
Tool Manager

Manages tools in the MCP server with passive CRUD operations (no per-manager locks).
"""

from __future__ import annotations

import logging
from typing import Any

from arcade_core.catalog import MaterializedTool, ToolCatalog
from arcade_core.schema import ToolDefinition

from arcade_mcp.exceptions import NotFoundError
from arcade_mcp.managers.base import ComponentManager
from arcade_mcp.types import Tool

logger = logging.getLogger("arcade.mcp.managers.tool")


class ToolManager(ComponentManager[MaterializedTool]):
    """
    Manages tools for the MCP server.

    Passive manager: no per-manager locks or start/stop lifecycle. The
    tool map is initialized from the provided catalog at construction time
    and kept in sync on add/update operations.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
        on_update=None,
    ):
        """
        Initialize tool manager.

        Args:
            catalog: Tool catalog to manage
            on_update: Optional callback invoked when an existing tool is updated.
        """
        super().__init__("tool", on_update)
        self.catalog = catalog
        self._tools_cache: dict[str, MaterializedTool] = {}

        # Seed cache from catalog
        for tool in self.catalog:
            self._tools_cache[tool.definition.fully_qualified_name] = tool
        logger.info(f"Tool manager initialized with {len(self._tools_cache)} tools")

    async def list_tools(self) -> list[Tool]:
        """
        List all available tools.

        Returns:
            List of MCP tool descriptions
        """
        tools: list[Tool] = []
        for tool in self._tools_cache.values():
            mcp_tool = Tool(
                name=tool.definition.fully_qualified_name,
                description=tool.definition.description,
                inputSchema={
                    "type": "object",
                    "properties": self._convert_parameters_to_schema(tool.definition),
                    "required": self._get_required_parameters(tool.definition),
                },
            )
            tools.append(mcp_tool)
        return tools

    async def get_tool(self, name: str) -> MaterializedTool:
        """
        Get a tool by name.

        Args:
            name: Tool name (fully qualified)

        Returns:
            The materialized tool

        Raises:
            NotFoundError: If tool not found
        """
        tool = self._tools_cache.get(name)
        if not tool:
            raise NotFoundError(f"Tool '{name}' not found")
        return tool

    async def add_tool(self, tool: MaterializedTool) -> None:
        """
        Add a tool to the manager.

        If a tool with the same name exists, equality is checked. If equal, the
        call is a no-op. If different, the tool is replaced and on_update is
        invoked.

        Args:
            tool: Tool to add
        """
        name = tool.definition.fully_qualified_name

        if name in self._tools_cache:
            existing = self._tools_cache[name]
            if self._tools_equal(existing, tool):
                return
            self._tools_cache[name] = tool
            # Also update in catalog
            self.catalog.add_tool(tool.tool, tool.definition.toolkit.name)
            self._on_update(name, existing, tool)
        else:
            self._tools_cache[name] = tool
            self.catalog.add_tool(tool.tool, tool.definition.toolkit.name)

    async def remove_tool(self, name: str) -> MaterializedTool:
        """
        Remove a tool from the manager.

        Args:
            name: Tool name (fully qualified)

        Returns:
            The removed tool

        Raises:
            NotFoundError: If tool not found
        """
        if name not in self._tools_cache:
            raise NotFoundError(f"Tool '{name}' not found")
        return self._tools_cache.pop(name)

    async def update_tool(self, name: str, tool: MaterializedTool) -> MaterializedTool:
        """
        Update an existing tool.

        Args:
            name: Current tool name
            tool: New tool to replace it with

        Returns:
            The updated tool

        Raises:
            NotFoundError: If tool not found
        """
        if name not in self._tools_cache:
            raise NotFoundError(f"Tool '{name}' not found")

        old_tool = self._tools_cache.pop(name)
        new_name = tool.definition.fully_qualified_name
        self._tools_cache[new_name] = tool
        self._on_update(new_name, old_tool, tool)
        return tool

    def _tools_equal(self, a: MaterializedTool, b: MaterializedTool) -> bool:
        """Compare tools by FQN, input and output models."""
        try:
            return (
                a.definition.fully_qualified_name == b.definition.fully_qualified_name
                and a.definition.input == b.definition.input
                and a.definition.output == b.definition.output
            )
        except Exception:
            return False

    def _convert_parameters_to_schema(self, definition: ToolDefinition) -> dict[str, Any]:
        """Convert tool parameters to JSON schema properties."""
        properties: dict[str, Any] = {}

        for param in definition.input.parameters:
            schema: dict[str, Any] = {
                "type": self._convert_value_type(param.value_schema.val_type),
            }

            if param.description:
                schema["description"] = param.description

            if param.value_schema.enum:
                schema["enum"] = param.value_schema.enum

            if param.value_schema.val_type == "array" and param.value_schema.inner_val_type:
                schema["items"] = {"type": self._convert_value_type(param.value_schema.inner_val_type)}

            if param.value_schema.val_type == "json" and param.value_schema.properties:
                schema["type"] = "object"
                schema["properties"] = {}
                for prop_name, prop_schema in param.value_schema.properties.items():
                    schema["properties"][prop_name] = {
                        "type": self._convert_value_type(prop_schema.val_type),
                    }
                    if prop_schema.description:
                        schema["properties"][prop_name]["description"] = prop_schema.description

            properties[param.name] = schema

        return properties

    def _get_required_parameters(self, definition: ToolDefinition) -> list[str]:
        """Get list of required parameter names."""
        return [param.name for param in definition.input.parameters if param.required]

    def _convert_value_type(self, val_type: str) -> str:
        """Convert arcade value type to JSON schema type."""
        type_map = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "json": "object",
            "array": "array",
        }
        return type_map.get(val_type, "string")