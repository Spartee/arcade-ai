"""
Tool Manager

Manages tools in the MCP server with passive CRUD operations (no per-manager locks).
"""

from __future__ import annotations

import logging

from arcade_core.catalog import MaterializedTool, ToolCatalog

from arcade_mcp.convert import build_input_schema_from_definition
from arcade_mcp.exceptions import NotFoundError
from arcade_mcp.managers.base import Registry
from arcade_mcp.types import Tool

logger = logging.getLogger("arcade.mcp.managers.tool")


class ToolManager(Registry[MaterializedTool]):
    """
    Manages tools for the MCP server.
    """

    def __init__(
        self,
        catalog: ToolCatalog,
    ):
        """
        Initialize tool manager.

        Args:
            catalog: Tool catalog to manage
        """
        super().__init__("tool")
        self.catalog = catalog

    async def list_tools(self) -> list[Tool]:
        """
        List all available tools.

        Returns:
            List of MCP tool descriptions
        """
        tools: list[Tool] = []
        for tool in self:
            input_schema = build_input_schema_from_definition(tool.definition)
            mcp_tool = Tool(
                name=tool.definition.fully_qualified_name.replace("_", "."),
                description=tool.definition.description,
                inputSchema=input_schema,
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
        if name not in self:
            raise NotFoundError(f"Tool {name} not found")
        return self.get(name)

    async def add_tool(self, tool: MaterializedTool) -> None:
        """
        Add a tool to the manager.

        Args:
            tool: Tool to add
        """
        self.add(tool.definition.fully_qualified_name, tool)
        # Also update in catalog
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
        return self.remove(name)

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
        # Remove old tool
        self.remove(name)
        # Add new tool with its new name (which may have changed)
        self.add(tool.definition.fully_qualified_name, tool)
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
