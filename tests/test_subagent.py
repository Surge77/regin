"""The search subagent.

The bound that matters most: it must not be able to edit or run commands. If it could,
"delegate the search" quietly becomes "delegate the work", and cost and permissions move
somewhere nobody is looking.
"""

from __future__ import annotations

from pathlib import Path

from support import ScriptedProvider

from regin.agent import build_tools
from regin.config import Config, LoopConfig, ModelConfig
from regin.schemas import ModelResponse, ToolCall, Usage
from regin.tools.subagent import SubagentArgs, SubagentTool


def _config(**loop_overrides: object) -> Config:
    return Config(
        model=ModelConfig(name="claude-sonnet-5"),
        loop=LoopConfig(**loop_overrides),  # pyright: ignore[reportArgumentType]
    )


def test_the_subagent_cannot_edit_or_run_commands(tmp_path: Path) -> None:
    """Read, glob, grep, outline — and deliberately nothing else."""
    tool = SubagentTool(tmp_path, ScriptedProvider([]), _config())
    names = tool._read_only_tools().names()  # pyright: ignore[reportPrivateUsage]

    assert names == ["glob", "grep", "outline", "read_file"]
    assert "bash" not in names
    assert "write_file" not in names
    assert "edit_file" not in names


async def test_returns_only_its_findings(tmp_path: Path) -> None:
    """The parent pays for the conclusion, not the search that produced it."""
    provider = ScriptedProvider(
        [ModelResponse(text="retry backoff is in providers/retry.py:88", usage=Usage())]
    )
    tool = SubagentTool(tmp_path, provider, _config())

    result = await tool.run(SubagentArgs(question="where is retry backoff computed?"))

    assert result == "retry backoff is in providers/retry.py:88"


async def test_the_question_reaches_the_subagent(tmp_path: Path) -> None:
    provider = ScriptedProvider([ModelResponse(text="found it")])
    await SubagentTool(tmp_path, provider, _config()).run(
        SubagentArgs(question="where is the parser?")
    )

    assert "where is the parser?" in provider.last_messages[1].content


async def test_it_is_capped_at_its_own_turn_limit(tmp_path: Path) -> None:
    """An unbounded subagent is just a second way to burn the budget."""
    searching = ModelResponse(
        tool_calls=[ToolCall(id="c1", name="glob", arguments={"pattern": "*.py"})],
        usage=Usage(input_tokens=10),
    )
    provider = ScriptedProvider([searching] * 20)

    result = await SubagentTool(tmp_path, provider, _config(subagent_max_turns=3)).run(
        SubagentArgs(question="find everything")
    )

    assert provider.calls == 3
    assert "no findings" in result


async def test_it_cannot_delegate_recursively(tmp_path: Path) -> None:
    """Otherwise one delegation can fan out into an unbounded tree of them."""
    provider = ScriptedProvider([ModelResponse(text="done")])
    tool = SubagentTool(tmp_path, provider, _config(subagent_tool=True))

    await tool.run(SubagentArgs(question="anything"))

    assert "search_agent" not in tool._read_only_tools().names()  # pyright: ignore[reportPrivateUsage]


async def test_a_subagent_failure_is_reported_not_raised(tmp_path: Path) -> None:
    """A failed search must be a recoverable message, not a dead parent run."""

    class BrokenProvider:
        name = "broken"

        async def complete(self, **kwargs: object) -> ModelResponse:
            raise ConnectionError("upstream down")

    result = await SubagentTool(tmp_path, BrokenProvider(), _config()).run(  # pyright: ignore[reportArgumentType]
        SubagentArgs(question="anything")
    )

    assert "subagent failed" in result


def test_the_tool_is_absent_unless_enabled(tmp_path: Path) -> None:
    """Every Phase 3 feature ships off so the baseline stays the measured baseline."""
    provider = ScriptedProvider([])

    off = build_tools(tmp_path, _config(), provider=provider)
    assert "search_agent" not in off.names()
    assert "outline" not in off.names()

    on = build_tools(
        tmp_path, _config(subagent_tool=True, outline_tool=True), provider=provider
    )
    assert "search_agent" in on.names()
    assert "outline" in on.names()


def test_the_subagent_tool_is_omitted_without_a_provider(tmp_path: Path) -> None:
    """It needs a provider to run its own loop; registering a broken one would fail later."""
    registry = build_tools(tmp_path, _config(subagent_tool=True), provider=None)
    assert "search_agent" not in registry.names()
