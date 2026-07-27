"""Summarise-and-continue compaction.

Long tasks die two ways: the request exceeds the context window, or the model loses the
thread of what it already tried and starts re-treading. Compaction replaces the middle of
the conversation with a summary while preserving the head (the task) and the tail (recent,
still-actionable detail).

Phase 3 discipline applies: this is the kind of change that must be re-scored before and
after, because it can plausibly hurt as well as help.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regin.schemas import Message

if TYPE_CHECKING:
    from regin.config import Config
    from regin.providers.base import Provider

# Messages kept verbatim at the end. Recent tool output is where the model does its work.
KEEP_RECENT = 6

SUMMARY_PROMPT = """Summarise the conversation so far for an engineer resuming this task.

Be specific and factual. Include:
- what the goal is,
- which files were read or changed, and what the change was,
- which commands were run and what they output,
- what has been ruled out, and why.

Omit anything that no longer affects the next step. Do not speculate about what to do next.
"""


class Compactor:
    def __init__(self, provider: Provider, config: Config) -> None:
        self._provider = provider
        self._config = config

    @property
    def _threshold(self) -> int:
        return int(self._config.loop.context_limit_tokens * self._config.loop.compact_at)

    async def maybe_compact(self, messages: list[Message], input_tokens: int) -> list[Message]:
        """Compact only when the last turn's real input token count crossed the threshold.

        The count comes from the provider's `usage`, not an estimate — compacting on a bad
        guess either fires constantly or never fires at all.
        """
        if input_tokens < self._threshold:
            return messages

        head, middle, tail = partition(messages)
        if not middle:
            return messages

        summary = await self._summarise(middle)
        return [*head, Message(role="user", content=f"<summary>\n{summary}\n</summary>"), *tail]

    async def _summarise(self, middle: list[Message]) -> str:
        response = await self._provider.complete(
            messages=[
                Message(role="system", content=SUMMARY_PROMPT),
                Message(role="user", content=transcribe(middle)),
            ],
            tools=[],
            model=self._config.model.name,
            max_output_tokens=2048,
            effort="low",
        )
        return response.text


def partition(messages: list[Message]) -> tuple[list[Message], list[Message], list[Message]]:
    """Split into (head, middle, tail).

    The head is the system prompt plus the original task — dropping it loses the goal.
    The tail boundary is nudged so it never begins on a `tool` message, which would orphan
    a tool result from the assistant turn that requested it and fail validation.
    """
    head_size = min(2, len(messages))
    head = messages[:head_size]
    rest = messages[head_size:]

    if len(rest) <= KEEP_RECENT:
        return head, [], rest

    split = len(rest) - KEEP_RECENT
    while split < len(rest) and rest[split].role == "tool":
        split += 1

    return head, rest[:split], rest[split:]


def transcribe(messages: list[Message]) -> str:
    """Flatten messages to plain text for the summariser.

    Provider-specific blocks are deliberately dropped — the summariser only needs the
    semantic content, and replaying thinking blocks into a fresh context is invalid.
    """
    lines: list[str] = []
    for message in messages:
        if message.content:
            lines.append(f"[{message.role}] {message.content}")
        for call in message.tool_calls:
            lines.append(f"[tool_call] {call.name}({call.arguments})")
        for result in message.tool_results:
            marker = "tool_error" if result.is_error else "tool_result"
            lines.append(f"[{marker}] {result.content}")
    return "\n".join(lines)
