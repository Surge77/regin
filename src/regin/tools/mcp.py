"""MCP client — third-party tools behind the same registry.

Phase 4.1. An MCP server exposes tools over a standard protocol; this adapts them into the
`Tool` shape the registry already speaks, so the loop cannot tell the difference and needs
no special case for them.

The seam is narrow on purpose. MCP tools arrive with a server-supplied JSON Schema rather
than a Pydantic model, so `mcp_args_model` builds a permissive model that validates nothing
beyond "is an object". The server is the authority on its own schema, and duplicating its
validation here would mean two sources of truth that drift.

The `mcp` SDK is an optional extra. Importing this module without it raises a clear error
rather than failing somewhere deep in a tool call.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from regin.tools.base import ToolRegistry

# MCP tools are third-party code reached over a network. They are never auto-approved:
# whatever the permission config says about them, "ask" is the floor.
MCP_TOOL_PREFIX = "mcp__"


class MCPUnavailableError(RuntimeError):
    """The `mcp` SDK is not installed."""


class MCPArgs(BaseModel):
    """Base for generated MCP argument models. Extra keys are allowed because the server's
    schema is authoritative and may be richer than anything inferred here."""

    model_config = ConfigDict(extra="allow")


def mcp_args_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Build a Pydantic model that carries the server's schema without re-implementing it.

    `model_json_schema()` is overridden to return the server's schema verbatim, so the
    provider sees exactly what the server declared — the registry's single-source-of-truth
    property is preserved even though the truth lives on the far side of the connection.
    """
    model = create_model(f"{name}Args", __base__=MCPArgs)

    def _schema(cls: type[BaseModel], **kwargs: Any) -> dict[str, Any]:
        return schema

    model.model_json_schema = classmethod(_schema)  # pyright: ignore[reportAttributeAccessIssue]
    return model


class MCPTool:
    """One tool exposed by an MCP server."""

    def __init__(self, session: Any, name: str, description: str, schema: dict[str, Any]) -> None:
        # Namespaced so a server cannot shadow a built-in tool. A remote `bash` that
        # silently replaced the local one would be a capability escalation.
        self.name = f"{MCP_TOOL_PREFIX}{name}"
        self.description = description
        self.args_model = mcp_args_model(name, schema)
        self._session = session
        self._remote_name = name

    async def run(self, args: BaseModel) -> str:
        result = await self._session.call_tool(self._remote_name, args.model_dump())
        return _render(result)


def _render(result: Any) -> str:
    """Flatten an MCP tool result to text.

    Non-text content (images, embedded resources) is described rather than dropped: the
    model needs to know something came back that it cannot see.
    """
    content = getattr(result, "content", None)
    if content is None:
        return str(result)

    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", "")
        if block_type == "text":
            parts.append(getattr(block, "text", ""))
        else:
            parts.append(f"[{block_type or 'unknown'} content omitted]")

    text = "\n".join(part for part in parts if part)
    if getattr(result, "isError", False):
        return f"error: {text}"
    return text or "(no output)"


async def register_mcp_tools(registry: ToolRegistry, session: Any) -> list[str]:
    """Discover a server's tools and add them to the registry. Returns the names added."""
    listing = await session.list_tools()
    added: list[str] = []

    for tool in listing.tools:
        mcp_tool = MCPTool(
            session=session,
            name=tool.name,
            description=tool.description or f"MCP tool {tool.name}",
            schema=tool.inputSchema or {"type": "object", "properties": {}},
        )
        registry.register(mcp_tool)
        added.append(mcp_tool.name)

    return added


def require_mcp() -> Any:
    """Import the MCP SDK, or explain how to get it."""
    try:
        import mcp  # pyright: ignore[reportMissingImports]
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise MCPUnavailableError(
            "the mcp SDK is not installed; install with `pip install regin[mcp]`"
        ) from exc
    return mcp
