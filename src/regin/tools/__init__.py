"""Tool registry.

One Pydantic model per tool: schema, validation, and wire payload from a single source.
"""

from regin.tools.base import Tool, ToolRegistry

__all__ = ["Tool", "ToolRegistry"]
