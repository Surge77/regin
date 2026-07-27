"""The Anthropic adapter's translation layer and the pricing table.

Message translation is where correctness bugs hide: a dropped thinking block or a split
tool-result batch produces a 400 or silently degrades behaviour many turns later.
"""

from __future__ import annotations

import pytest

from regin.providers.anthropic import (
    assistant_content,
    cache_system,
    cache_tools,
    split_system,
    tool_result_blocks,
)
from regin.providers.base import PRICING, cost_usd
from regin.schemas import Message, ToolCall, ToolResult


def test_system_messages_are_lifted_out_of_the_conversation() -> None:
    """Anthropic takes the system prompt as a top-level parameter, not a message."""
    system, turns = split_system(
        [
            Message(role="system", content="be careful"),
            Message(role="user", content="fix it"),
        ]
    )
    assert system == "be careful"
    assert turns == [{"role": "user", "content": "fix it"}]


def test_tool_results_are_sent_as_a_single_user_turn() -> None:
    """Splitting results across messages teaches the model to stop calling tools in
    parallel, which silently slows every later run."""
    _, turns = split_system(
        [
            Message(
                role="tool",
                tool_results=[
                    ToolResult(call_id="a", content="one"),
                    ToolResult(call_id="b", content="two"),
                ],
            )
        ]
    )
    assert len(turns) == 1
    assert turns[0]["role"] == "user"
    content = turns[0]["content"]
    assert not isinstance(content, str)
    assert len(list(content)) == 2


def test_an_errored_tool_result_is_marked_as_such() -> None:
    blocks = tool_result_blocks(
        Message(role="tool", tool_results=[ToolResult(call_id="a", content="boom", is_error=True)])
    )
    assert blocks[0]["is_error"] is True


def test_an_empty_tool_result_is_given_placeholder_text() -> None:
    """The API rejects an empty tool_result body; a silent command still needs a result."""
    blocks = tool_result_blocks(
        Message(role="tool", tool_results=[ToolResult(call_id="a", content="")])
    )
    assert blocks[0]["content"] == "(no output)"


def test_assistant_turns_replay_raw_provider_blocks() -> None:
    """Thinking blocks must be echoed back byte-identical, so they cannot be rebuilt."""
    blocks = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "hi"}]
    message = Message(role="assistant", content="hi", provider_blocks=blocks)
    assert assistant_content(message) == blocks


def test_assistant_turns_are_reconstructed_when_no_raw_blocks_exist() -> None:
    """Scripted and replayed conversations have no provider blocks to echo."""
    message = Message(
        role="assistant",
        content="calling a tool",
        tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})],
    )
    content = assistant_content(message)
    assert content[0] == {"type": "text", "text": "calling a tool"}
    assert content[1]["type"] == "tool_use"
    assert content[1]["name"] == "bash"


def test_an_empty_assistant_turn_becomes_an_empty_string() -> None:
    """An empty content list is rejected by the API."""
    assert assistant_content(Message(role="assistant")) == ""


def test_the_system_prompt_carries_a_cache_breakpoint() -> None:
    """The prompt is frozen for the run, so every turn after the first reads it at 0.1x."""
    blocks = cache_system("stable prompt")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_an_absent_system_prompt_produces_no_blocks() -> None:
    assert cache_system("") == []


def test_the_last_tool_definition_carries_a_cache_breakpoint() -> None:
    """Tools render before the system prompt, so they belong inside the cached prefix."""
    tools = cache_tools([{"name": "a"}, {"name": "b"}])
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}


def test_caching_does_not_mutate_the_caller_s_tool_definitions() -> None:
    """The registry rebuilds this list each turn; mutating it would compound breakpoints."""
    original = [{"name": "a"}]
    cache_tools(original)
    assert original == [{"name": "a"}]


def test_no_tools_produces_no_blocks() -> None:
    assert cache_tools([]) == []


def test_cost_separates_cache_token_classes() -> None:
    """Cache reads are 0.1x input and writes 1.25x; flattening them corrupts the metric."""
    cost = cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(3.00)

    cached = cost_usd("claude-sonnet-5", input_tokens=0, output_tokens=0, cache_read=1_000_000)
    assert cached == pytest.approx(0.30)

    written = cost_usd(
        "claude-sonnet-5", input_tokens=0, output_tokens=0, cache_read=0, cache_write=1_000_000
    )
    assert written == pytest.approx(3.75)


def test_an_unpriced_model_reports_zero_rather_than_guessing() -> None:
    """Cost must come from the pricing table, never from an estimate."""
    assert cost_usd("some-unreleased-model", 1_000_000, 1_000_000) == 0.0


@pytest.mark.parametrize("model", sorted(PRICING))
def test_every_priced_model_defines_all_four_token_classes(model: str) -> None:
    assert set(PRICING[model]) == {"input", "output", "cache_read", "cache_write"}


def test_cache_rates_follow_the_published_multipliers() -> None:
    """cache_read is 0.1x input and cache_write 1.25x — a typo here silently skews cost."""
    for model, rates in PRICING.items():
        assert rates["cache_read"] == pytest.approx(rates["input"] * 0.1), model
        assert rates["cache_write"] == pytest.approx(rates["input"] * 1.25), model
