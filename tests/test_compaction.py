"""Context compaction.

The invariant that matters most: compaction must never orphan a tool result from the
assistant turn that requested it, or the next request fails validation outright.
"""

from __future__ import annotations

from support import ScriptedProvider

from regin.config import Config, LoopConfig, ModelConfig
from regin.context.compaction import KEEP_RECENT, Compactor, partition, transcribe
from regin.schemas import Message, ModelResponse, ToolCall, ToolResult


def _config(limit: int = 1000, compact_at: float = 0.75) -> Config:
    return Config(
        model=ModelConfig(name="claude-sonnet-5"),
        loop=LoopConfig(context_limit_tokens=limit, compact_at=compact_at),
    )


def _conversation(turns: int) -> list[Message]:
    messages = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="the task"),
    ]
    for i in range(turns):
        messages.append(
            Message(
                role="assistant",
                content=f"step {i}",
                tool_calls=[ToolCall(id=f"c{i}", name="bash", arguments={"command": "ls"})],
            )
        )
        messages.append(
            Message(role="tool", tool_results=[ToolResult(call_id=f"c{i}", content=f"out {i}")])
        )
    return messages


async def test_does_nothing_below_the_threshold() -> None:
    provider = ScriptedProvider([])
    messages = _conversation(20)
    result = await Compactor(provider, _config()).maybe_compact(messages, input_tokens=100)

    assert result == messages
    assert provider.calls == 0


async def test_compacts_once_the_threshold_is_crossed() -> None:
    provider = ScriptedProvider([ModelResponse(text="a summary of the work so far")])
    messages = _conversation(20)

    result = await Compactor(provider, _config(limit=1000)).maybe_compact(messages, 800)

    assert len(result) < len(messages)
    assert any("a summary of the work so far" in m.content for m in result)


async def test_preserves_the_system_prompt_and_the_original_task() -> None:
    """Dropping the head loses the goal, and the agent starts solving something else."""
    provider = ScriptedProvider([ModelResponse(text="summary")])
    messages = _conversation(20)

    result = await Compactor(provider, _config()).maybe_compact(messages, 800)

    assert result[0].role == "system"
    assert result[1].content == "the task"


async def test_keeps_the_most_recent_turns_verbatim() -> None:
    provider = ScriptedProvider([ModelResponse(text="summary")])
    messages = _conversation(20)

    result = await Compactor(provider, _config()).maybe_compact(messages, 800)

    assert result[-1].tool_results == messages[-1].tool_results


async def test_summarises_with_low_effort() -> None:
    """The summariser is a supporting call; paying high effort for it is waste."""
    provider = ScriptedProvider([ModelResponse(text="summary")])
    await Compactor(provider, _config()).maybe_compact(_conversation(20), 800)

    assert provider.last_effort == "low"


async def test_a_short_conversation_is_left_alone() -> None:
    """With nothing between head and tail there is nothing to summarise."""
    provider = ScriptedProvider([])
    messages = _conversation(1)

    result = await Compactor(provider, _config()).maybe_compact(messages, 900)

    assert result == messages
    assert provider.calls == 0


def test_the_tail_never_starts_on_an_orphaned_tool_result() -> None:
    """A tool message without its assistant turn fails provider validation."""
    for turns in range(1, 15):
        _, _, tail = partition(_conversation(turns))
        assert not tail or tail[0].role != "tool"


def test_the_tail_keeps_at_least_the_recent_window() -> None:
    _, _, tail = partition(_conversation(20))
    assert len(tail) >= KEEP_RECENT


def test_transcription_includes_calls_and_results() -> None:
    """The summariser needs what was run and what came back, not just the prose."""
    text = transcribe(
        [
            Message(
                role="assistant",
                content="running tests",
                tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "pytest"})],
            ),
            Message(
                role="tool",
                tool_results=[ToolResult(call_id="c1", content="2 failed", is_error=True)],
            ),
        ]
    )

    assert "running tests" in text
    assert "bash" in text
    assert "tool_error" in text
    assert "2 failed" in text


def test_transcription_drops_provider_blocks() -> None:
    """Replaying thinking blocks into a fresh summariser context is invalid."""
    text = transcribe(
        [Message(role="assistant", content="hi", provider_blocks=[{"type": "thinking"}])]
    )
    assert "thinking" not in text
