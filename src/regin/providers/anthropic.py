"""Anthropic adapter.

Two details drive the shape of this file:

1. `usage` is passed through unflattened. Cost is the headline benchmark metric, so
   cache-read and cache-write tokens — priced at 0.1x and 1.25x input — have to reach
   the trace layer as distinct classes, not summed into one number.
2. Assistant turns are replayed as the raw blocks the API returned. Thinking is on by
   default on the Claude 5 family, and thinking blocks must be echoed back byte-identical
   on the following turn; a normalised Message cannot reconstruct them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import (
    MessageParam,
    OutputConfigParam,
    TextBlockParam,
    ToolUnionParam,
)

from regin.schemas import Message, ModelResponse, ToolCall, Usage

# Sampling parameters (temperature / top_p / top_k) are rejected with a 400 on the
# Claude 5 family; `effort` replaces them as the depth control.
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, on_text: Callable[[str], None] | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._on_text = on_text

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ) -> ModelResponse:
        started = time.monotonic()
        system, turns = split_system(messages)

        async with self._client.messages.stream(
            model=model,
            max_tokens=max_output_tokens,
            system=cast("list[TextBlockParam]", cache_system(system)),
            tools=cast("list[ToolUnionParam]", cache_tools(tools)),
            output_config=cast(OutputConfigParam, {"effort": effort}),
            messages=cast("list[MessageParam]", turns),
        ) as stream:
            if self._on_text is not None:
                async for chunk in stream.text_stream:
                    self._on_text(chunk)
            final = await stream.get_final_message()

        return ModelResponse(
            text="".join(b.text for b in final.content if b.type == "text"),
            tool_calls=[
                ToolCall(id=b.id, name=b.name, arguments=dict(b.input))  # pyright: ignore[reportArgumentType]
                for b in final.content
                if b.type == "tool_use"
            ],
            usage=Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
                cache_read_tokens=final.usage.cache_read_input_tokens or 0,
                cache_write_tokens=final.usage.cache_creation_input_tokens or 0,
            ),
            stop_reason=final.stop_reason or "",
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_blocks=[b.model_dump() for b in final.content],
        )


def split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Separate the system prompt from the conversation.

    Anthropic takes the system prompt as a top-level parameter rather than a message,
    and it must stay byte-identical across turns or the cached prefix is invalidated.
    """
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        elif message.role == "assistant":
            content = assistant_content(message)
            turns.append({"role": "assistant", "content": content})
        elif message.role == "tool":
            turns.append({"role": "user", "content": tool_result_blocks(message)})
        else:
            turns.append({"role": "user", "content": message.content})

    return "\n".join(system_parts), turns


def assistant_content(message: Message) -> Any:
    """Replay the exact blocks the API returned when we have them.

    Reconstructing an assistant turn from text + tool calls drops thinking blocks,
    which the API rejects on the next request.
    """
    if message.provider_blocks:
        return message.provider_blocks

    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks.extend(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        for call in message.tool_calls
    )
    return blocks or ""


def tool_result_blocks(message: Message) -> list[dict[str, Any]]:
    """All results for one assistant turn go back in a single user message.

    Splitting them across messages teaches the model to stop making parallel tool calls.
    """
    return [
        {
            "type": "tool_result",
            "tool_use_id": result.call_id,
            "content": result.content or "(no output)",
            "is_error": result.is_error,
        }
        for result in message.tool_results
    ]


def cache_system(system: str) -> list[dict[str, Any]]:
    """Mark the system prompt as a cache breakpoint.

    The prompt is frozen for the run's lifetime, so every turn after the first reads it
    at 0.1x input price instead of paying full rate on each iteration.
    """
    if not system:
        return []
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def cache_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cache the tool definitions too. They render before the system prompt and never
    change within a run, so they belong inside the cached prefix."""
    if not tools:
        return []
    marked = [dict(tool) for tool in tools]
    marked[-1]["cache_control"] = {"type": "ephemeral"}
    return marked
