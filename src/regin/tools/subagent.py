"""Bounded search subagent.

Phase 3.3. Exploration is what fills a context window: a dozen greps and file reads to
locate one function leaves the main conversation carrying every dead end. A subagent runs
that search in its own context and returns only the answer, so the parent pays for the
conclusion rather than the search.

Two hard bounds, because an unbounded subagent is just a second way to burn the budget:
a turn cap, and a read-only tool set. It cannot edit and it cannot run commands — if it
could, "delegate the search" would quietly become "delegate the work", and the cost and
permission story would move somewhere nobody is looking.

Ships behind `loop.subagent_tool`, off by default, like every Phase 3 change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from regin.tools.base import ToolRegistry
from regin.tools.fs import ReadFileTool
from regin.tools.outline import OutlineTool
from regin.tools.search import GlobTool, GrepTool

if TYPE_CHECKING:
    from regin.config import Config
    from regin.providers.base import Provider

SUBAGENT_PROMPT = """You are a search subagent. You can read and search the repository; you
cannot edit files or run commands.

Answer the question you are given as specifically as you can: name files, line numbers, and
symbols. Quote the few lines that actually matter rather than whole files. If you cannot find
the answer, say what you searched and what you ruled out — that is useful too.

Be brief. Your entire reply is inserted into another agent's context.
"""


class SubagentArgs(BaseModel):
    question: str = Field(
        description="A specific search question, e.g. 'where is retry backoff computed?'"
    )


class SubagentTool:
    name = "search_agent"
    description = (
        "Delegate a search question to a subagent with its own context. It can read, glob, "
        "grep, and outline, but cannot edit or run commands. Returns only its findings. "
        "Use this for open-ended exploration that would otherwise fill this conversation."
    )
    args_model = SubagentArgs

    def __init__(self, root: Path, provider: Provider, config: Config) -> None:
        self._root = root
        self._provider = provider
        self._config = config

    def _read_only_tools(self) -> ToolRegistry:
        """Deliberately excludes write, edit, and bash — see the module docstring."""
        registry = ToolRegistry()
        registry.register(ReadFileTool(self._root))
        registry.register(GlobTool(self._root))
        registry.register(GrepTool(self._root))
        registry.register(OutlineTool(self._root))
        return registry

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, SubagentArgs)

        # Imported here: loop.py imports the tool registry, so a module-level import would
        # close a cycle.
        from regin.loop import AgentLoop
        from regin.permissions import Permissions

        config = self._config.model_copy(deep=True)
        config.loop.max_turns = self._config.loop.subagent_max_turns
        config.loop.plan_first = False  # a search does not need a plan
        config.loop.subagent_tool = False  # no recursive delegation

        loop = AgentLoop(
            provider=self._provider,
            tools=self._read_only_tools(),
            # Read-only tools only, so auto-approval grants nothing the parent lacks.
            permissions=Permissions({}, auto_approve=True),
            config=config,
        )

        record = await loop.run(f"{SUBAGENT_PROMPT}\n\nQuestion: {args.question}")

        if record.error:
            return f"subagent failed: {record.error}"
        return record.summary or "subagent returned no findings"
