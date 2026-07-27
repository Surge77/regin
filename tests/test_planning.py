"""The planning pass.

Ships disabled. These tests pin the flag's semantics so that when someone finally A/Bs it,
the thing being measured is the plan itself and not an accidental change to the baseline.
"""

from __future__ import annotations

from support import ScriptedProvider

from regin.config import Config, LoopConfig, ModelConfig
from regin.context.planning import PLAN_PREAMBLE, Planner
from regin.schemas import Message, ModelResponse


def _config(plan_first: bool = True, effort: str = "medium") -> Config:
    return Config(
        model=ModelConfig(name="claude-sonnet-5"),
        loop=LoopConfig(plan_first=plan_first, planner_effort=effort),  # pyright: ignore[reportArgumentType]
    )


def _messages() -> list[Message]:
    return [
        Message(role="system", content="system"),
        Message(role="user", content="fix the parser"),
    ]


async def test_disabled_by_default_leaves_the_conversation_untouched() -> None:
    """The shipped baseline must stay the measured baseline until a sweep says otherwise."""
    config = Config()
    assert config.loop.plan_first is False

    provider = ScriptedProvider([])
    seeded = await Planner(provider, config).seed(_messages(), "fix the parser")

    assert seeded == _messages()
    assert provider.calls == 0


async def test_enabled_it_prepends_a_plan_turn() -> None:
    provider = ScriptedProvider([ModelResponse(text="1. read the parser\n2. fix it")])
    seeded = await Planner(provider, _config()).seed(_messages(), "fix the parser")

    assert len(seeded) == 3
    assert "read the parser" in seeded[-1].content


async def test_the_plan_is_an_assistant_turn_not_an_instruction() -> None:
    """As a system message the model treats the plan as binding and follows it past the
    point where evidence contradicts it."""
    provider = ScriptedProvider([ModelResponse(text="a plan")])
    seeded = await Planner(provider, _config()).seed(_messages(), "task")

    assert seeded[-1].role == "assistant"
    assert seeded[-1].content.startswith(PLAN_PREAMBLE)


async def test_an_empty_plan_is_not_injected() -> None:
    """A blank turn wastes context and teaches nothing."""
    provider = ScriptedProvider([ModelResponse(text="   ")])
    seeded = await Planner(provider, _config()).seed(_messages(), "task")

    assert seeded == _messages()


async def test_the_planner_sees_no_tools() -> None:
    """The planner cannot act — offering tools invites it to start editing."""
    provider = ScriptedProvider([ModelResponse(text="a plan")])
    await Planner(provider, _config()).plan("task")

    assert provider.last_messages[0].role == "system"
    assert "cannot edit anything yet" in provider.last_messages[0].content


async def test_planner_effort_is_configurable_separately() -> None:
    """Planning depth is its own A/B knob, independent of the main loop's effort."""
    provider = ScriptedProvider([ModelResponse(text="a plan")])
    await Planner(provider, _config(effort="low")).plan("task")

    assert provider.last_effort == "low"


async def test_the_task_reaches_the_planner_verbatim() -> None:
    provider = ScriptedProvider([ModelResponse(text="a plan")])
    await Planner(provider, _config()).plan("make the failing test pass")

    assert provider.last_messages[1].content == "make the failing test pass"
