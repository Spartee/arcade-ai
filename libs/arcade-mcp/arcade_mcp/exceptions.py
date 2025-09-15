"""
MCP Exception Hierarchy

Provides domain-specific exceptions for better error handling and debugging.
"""


class MCPError(Exception):
    """Base error for all MCP-related exceptions."""
    pass


class ValidationError(MCPError):
    """Error in validating parameters or return values."""
    pass


class ToolError(MCPError):
    """Error in tool operations."""
    pass


class ResourceError(MCPError):
    """Error in resource operations."""
    pass


class PromptError(MCPError):
    """Error in prompt operations."""
    pass


class NotFoundError(MCPError):
    """Requested entity not found."""
    pass


class AuthorizationError(MCPError):
    """Authorization failure."""
    pass


class TransportError(MCPError):
    """Error in transport layer operations."""
    pass


class SessionError(MCPError):
    """Error in session management."""
    pass


class ProtocolError(MCPError):
    """MCP protocol violation or error."""
    pass


class ConfigurationError(MCPError):
    """Configuration error."""
    pass


class DuplicateError(MCPError):
    """Duplicate entity registration."""
    pass


class TimeoutError(MCPError):
    """Operation timeout."""
    pass


class DisabledError(MCPError):
    """Entity is disabled."""
    pass