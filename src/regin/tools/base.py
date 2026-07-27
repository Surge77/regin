"""Tool protocol and registry.

A tool declares its arguments as a Pydantic model. `model_json_schema()` then produces
the exact JSON Schema providers want, so the signature is written once and serves as
validation, documentation, and wire format simultaneously.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class Tool(Protocol):
    """Declared with read-only members on purpose.

    As mutable attributes these would be invariant, so a tool whose `args_model` is
    `type[ReadFileArgs]` would not satisfy `type[BaseModel]` and every concrete tool
    would fail the type gate.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def args_model(self) -> type[BaseModel]: ...

    async def run(self, args: BaseModel) -> str: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in the shape providers expect."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.args_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


def truncate(output: str, limit: int) -> str:
    """Tool output must never blow the context window."""
    if len(output) <= limit:
        return output
    half = limit // 2
    omitted = len(output) - limit
    return f"{output[:half]}\n\n... [{omitted} chars omitted] ...\n\n{output[-half:]}"
