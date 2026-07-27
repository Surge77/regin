"""Shared test fixtures.

`ScriptedProvider` is the stub for the provider boundary. Unit tests never make a network
call; anything below the provider seam runs for real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from regin.config import Config
from regin.schemas import Message, ModelResponse, ToolResult
from regin.tools.base import ToolRegistry
from regin.tools.fs import EditFileTool, ReadFileTool, WriteFileTool
from regin.tools.search import GlobTool, GrepTool
from regin.tools.shell import BashTool


class ScriptedProvider:
    """Replays a fixed list of responses and records what the loop sent back.

    Recording the tool results is what makes assertions meaningful: it proves the agent
    actually observed the failing test output before it edited the file.
    """

    name = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.received: list[list[ToolResult]] = []
        self.last_effort: str = ""
        self.last_messages: list[Message] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ) -> ModelResponse:
        self.last_effort = effort
        self.last_messages = list(messages)
        if messages and messages[-1].role == "tool":
            self.received.append(messages[-1].tool_results)

        if self.calls >= len(self._responses):
            raise AssertionError("ScriptedProvider ran out of responses — the loop over-ran")

        response = self._responses[self.calls]
        self.calls += 1
        return response


def build_test_tools(root: Path, config: Config) -> ToolRegistry:
    """The real tool set, rooted at a temp directory."""
    registry = ToolRegistry()
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(GlobTool(root))
    registry.register(GrepTool(root))
    registry.register(
        BashTool(
            root,
            timeout_s=config.tools.bash_timeout_s,
            allowlist=tuple(config.tools.bash_allowlist),
            max_output_chars=config.tools.max_tool_output_chars,
        )
    )
    return registry
