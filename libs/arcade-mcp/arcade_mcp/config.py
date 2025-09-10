"""Configuration models and helpers for arcade_mcp Server."""

# Standard library imports
from pathlib import Path
from typing import Any

# Third-party imports
import dotenv
from dotenv import dotenv_values
import toml
from pydantic import BaseModel, ConfigDict, Field

from arcade_core.toolkit import Toolkit


class ServerConfig(BaseModel):
    """High-level MCP server configuration."""
    log_level: str = Field(default="INFO", description="Log level")
    rate_limit_per_min: int = Field(default=60, description="Rate limit per minute")
    debounce_ms: int = Field(default=100, description="Debounce milliseconds")
    max_queue: int = Field(default=1000, description="Max queue size")
    cleanup_interval_sec: int = Field(default=10, description="Cleanup interval seconds")
    max_sessions: int = Field(default=1000, description="Max sessions")
    session_timeout_sec: int = Field(default=300, description="Session timeout seconds")

class ServerContext(BaseModel):
    """Local Context for the MCP server."""

    user_id: str = Field(default="", description="User ID") # get from ARCADE_USER_ID env var
    secrets: dict[str, Any] = Field(default_factory=dict, description="Secrets")

    # Allow arbitrary types (e.g., ModuleType) for convenience in local dev scenarios
    model_config = ConfigDict(arbitrary_types_allowed=True)

class ServerToolkitMetadata(BaseModel):

    name: str = Field(default="ArcadeMCP", description="Toolkit name")
    version: str = Field(default="0.1.0", description="Toolkit version")
    description: str = Field(default="Arcade Model Context Protocol (MCP) toolkit", description="Toolkit description")

    # Optional explicit overrides (normally discovered from pyproject)
    package_name: str | None = Field(default=None, description="Toolkit package name")
    author: list[str] | None = Field(default=None, description="Authors list")
    repository: str | None = Field(default=None, description="Repository URL")
    homepage: str | None = Field(default=None, description="Homepage URL")

    # Allow arbitrary types (e.g., ModuleType) for convenience in local dev scenarios
    model_config = ConfigDict(arbitrary_types_allowed=True)


class MCPConfig(BaseModel):
    """High-level MCP server configuration.

    Will override with values from .env file if present.
    ServerToolkitMetdata will come from pyproject.toml if present.
    """

    server: ServerConfig = Field(default=ServerConfig())
    tool_metadata: ServerToolkitMetadata = Field(default=ServerToolkitMetadata())
    enable_logging: bool = Field(default=True, description="Enable logging")

    arcade_api_key: str | None = Field(default=None, description="Arcade API key")
    arcade_api_url: str | None = Field(default=None, description="Arcade API URL")

    disable_auth: bool = Field(default=True, description="Disable authentication")
    server_secret: str = Field(default="dev", description="Server secret")

    # Environment variables
    env_file: str = Field(default=".env", description="Environment file")
    context: ServerContext = Field(default=ServerContext(), description="Local server context")

    # Allow arbitrary types (e.g., ModuleType) for convenience in local dev scenarios
    model_config = ConfigDict(arbitrary_types_allowed=True)


def _load_pyproject_metadata(cwd: Path) -> dict[str, Any]:
    """Load selected metadata from pyproject.toml if present."""
    meta: dict[str, Any] = {}
    pyproject_path = cwd / "pyproject.toml"
    if not pyproject_path.exists():
        return meta

    try:
        data = toml.load(pyproject_path)
        project = data.get("project", {})
        meta["name"] = project.get("name")
        meta["version"] = project.get("version")
        meta["description"] = project.get("description")
        urls = project.get("urls", {})
        meta["homepage"] = urls.get("Homepage")
        meta["repository"] = urls.get("Repository")
        authors = project.get("authors", [])
        meta["author"] = [a.get("name", "") for a in authors if isinstance(a, dict)]
    except Exception:
        # Best effort: ignore pyproject parsing errors for local dev ergonomics
        pass

    return meta

def _load_env_file(env_file: str | Path) -> dict[str, Any]:
    """Load the environment file and return a mapping of values."""
    if not env_file:
        return {}
    # Load into process env for downstream consumers
    dotenv.load_dotenv(env_file)
    # Return a mapping for direct lookups
    values = dotenv_values(env_file)
    return dict(values) if values else {}


def init_server_toolkit(env_file: str | Path | None = None, pyproject_path: Path | None = None) -> Toolkit:
    """Initialize the server toolkit from the config.

    If pyproject.toml is present, use the metadata from that.
    if env file is present override any values from pyproject.toml
    if they collide, the env file values will take precedence.

    Returns:
        Toolkit: The server toolkit
    """
    meta = _load_pyproject_metadata(pyproject_path or Path.cwd())
    env = _load_env_file(env_file or ".env")

    name = env.get("MCP_SERVER_NAME") or meta.get("name") or "ArcadeMCP"
    package_name = env.get("MCP_SERVER_PACKAGE_NAME") or meta.get("name") or name
    version = env.get("MCP_SERVER_VERSION") or meta.get("version") or "0.1.0"
    description = env.get("MCP_SERVER_DESCRIPTION") or meta.get("description") or "Arcade MCP Server"
    author = env.get("MCP_SERVER_AUTHOR") or meta.get("author") or []
    repository = env.get("MCP_SERVER_REPOSITORY") or meta.get("repository")
    homepage = env.get("MCP_SERVER_HOMEPAGE") or meta.get("homepage")

    return Toolkit(
        name=name,
        package_name=package_name,
        version=version,
        description=description,
        author=author,
        repository=repository,
        homepage=homepage,
    )

