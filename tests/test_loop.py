"""The loop's termination guards.

`loop.py` is the deliverable, so every exit path is covered: a loop that fails to stop
costs real money, and one that stops for the wrong reason corrupts the benchmark.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel
from support import ScriptedProvider

from regin.config import Config, LoopConfig, ModelConfig
from regin.loop import AgentLoop, summarise_call
from regin.permissions import Permissions
from regin.schemas import ModelResponse, Outcome, ToolCall, Usage
from regin.tools.base import ToolRegistry


class ExplodingArgs(BaseModel):
    value: int


class ExplodingTool:
    name = "explode"
    description = "Always raises."
    args_model = ExplodingArgs

    async def run(self, args: BaseModel) -> str:
        raise RuntimeError("tool blew up")


class AlwaysFailingProvider:
    name = "failing"

    async def complete(self, *args: object, **kwargs: object) -> ModelResponse:
        raise ConnectionError("provider unreachable")


def _config(**loop_overrides: object) -> Config:
    return Config(
        model=ModelConfig(name="claude-sonnet-5"),
        loop=LoopConfig(**loop_overrides),  # pyright: ignore[reportArgumentType]
        permissions={"explode": "allow"},
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ExplodingTool())
    return registry


def _tool_turn(name: str = "explode", arguments: dict[str, object] | None = None) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id="c1", name=name, arguments=arguments or {"value": 1})],
        usage=Usage(input_tokens=10, output_tokens=5),
    )


async def test_stops_at_the_turn_limit() -> None:
    config = _config(max_turns=3, max_run_cost_usd=100.0)
    provider = ScriptedProvider([_tool_turn() for _ in range(10)])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert record.outcome is Outcome.TURN_LIMIT
    assert record.turns == 3


async def test_stops_when_the_run_budget_is_exhausted() -> None:
    """A per-run ceiling is the last line of defence against a runaway loop."""
    config = _config(max_turns=100, max_run_cost_usd=0.0001)
    expensive = ModelResponse(
        tool_calls=[ToolCall(id="c1", name="explode", arguments={"value": 1})],
        usage=Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    provider = ScriptedProvider([expensive] * 10)
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert record.outcome is Outcome.BUDGET_EXCEEDED
    assert record.cost_usd >= config.loop.max_run_cost_usd


async def test_a_provider_failure_ends_the_run_with_the_reason() -> None:
    config = _config()
    loop = AgentLoop(
        AlwaysFailingProvider(), _registry(), Permissions(config.permissions), config
    )

    record = await loop.run("go")

    assert record.outcome is Outcome.ERROR
    assert "ConnectionError" in (record.error or "")
    assert record.turns == 0


async def test_an_unknown_tool_is_reported_back_not_raised() -> None:
    config = _config()
    provider = ScriptedProvider([_tool_turn(name="nonexistent"), ModelResponse(text="ok")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert provider.received[0][0].is_error
    assert "unknown tool" in provider.received[0][0].content
    assert record.outcome is Outcome.RESOLVED


async def test_invalid_tool_arguments_are_reported_back() -> None:
    """Schema validation failures are the model's mistake to correct, not a crash."""
    config = _config()
    provider = ScriptedProvider(
        [_tool_turn(arguments={"value": "not-an-int"}), ModelResponse(text="ok")]
    )
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    await loop.run("go")

    assert provider.received[0][0].is_error
    assert "invalid arguments" in provider.received[0][0].content


async def test_a_tool_that_raises_is_reported_back() -> None:
    config = _config()
    provider = ScriptedProvider([_tool_turn(), ModelResponse(text="ok")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    await loop.run("go")

    assert provider.received[0][0].is_error
    assert "tool blew up" in provider.received[0][0].content


async def test_no_tool_calls_means_the_model_considers_the_task_done() -> None:
    config = _config()
    provider = ScriptedProvider([ModelResponse(text="all set", usage=Usage(input_tokens=5))])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert record.outcome is Outcome.RESOLVED
    assert record.turns == 1


async def test_usage_accumulates_across_turns() -> None:
    """Cost is the headline metric; per-turn usage must sum, not overwrite."""
    config = _config()
    provider = ScriptedProvider([_tool_turn(), _tool_turn(), ModelResponse(text="done")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert record.usage.input_tokens == 20
    assert record.usage.output_tokens == 10


async def test_the_system_prompt_and_task_open_the_conversation() -> None:
    config = _config()
    provider = ScriptedProvider([ModelResponse(text="done")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    await loop.run("fix the parser")

    assert provider.last_messages[0].role == "system"
    assert provider.last_messages[1].content == "fix the parser"


async def test_the_configured_effort_reaches_the_provider() -> None:
    config = _config()
    config.model.effort = "low"
    provider = ScriptedProvider([ModelResponse(text="done")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    await loop.run("go")

    assert provider.last_effort == "low"


async def test_the_record_pins_the_config_hash() -> None:
    """A/B comparison is only honest if a result names the configuration it came from."""
    config = _config()
    provider = ScriptedProvider([ModelResponse(text="done")])
    loop = AgentLoop(provider, _registry(), Permissions(config.permissions), config)

    record = await loop.run("go")

    assert record.config_hash
    assert record.model == "claude-sonnet-5"


def test_tool_call_summaries_are_truncated_for_the_prompt() -> None:
    call = ToolCall(id="c1", name="bash", arguments={"command": "x" * 500})
    summary = summarise_call(call)
    assert summary.startswith("bash(command=")
    assert len(summary) < 200


@pytest.mark.parametrize("quiet", [True, False])
async def test_runs_with_or_without_a_renderer(tmp_path: Path, quiet: bool) -> None:
    from regin.ui import Renderer

    config = _config()
    provider = ScriptedProvider([_tool_turn(), ModelResponse(text="done")])
    loop = AgentLoop(
        provider,
        _registry(),
        Permissions(config.permissions),
        config,
        renderer=Renderer(quiet=quiet),
    )

    record = await loop.run("go")
    assert record.outcome is Outcome.RESOLVED
