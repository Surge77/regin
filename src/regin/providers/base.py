"""The provider seam.

Deliberately not litellm: cost is the headline metric, so per-provider `usage` blocks
(including cache-read/cache-write token classes) must reach the trace layer unflattened.
"""

from __future__ import annotations

from typing import Any, Protocol

from regin.schemas import Message, ModelResponse


class Provider(Protocol):
    """Every adapter implements exactly this.

    `name` is read-only so that a decorator exposing it as a property — `RetryingProvider`
    does — still satisfies the protocol. A mutable attribute would be invariant and reject it.
    """

    @property
    def name(self) -> str: ...

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
        effort: str,
    ) -> ModelResponse: ...


# USD per million tokens. Pinned deliberately — a silent price change would
# corrupt every historical cost comparison. Cache reads are 0.1x input and cache
# writes 1.25x input, per the published 5-minute-TTL rates.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0,
             cache_write: int = 0) -> float:
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read * rates["cache_read"]
        + cache_write * rates["cache_write"]
    ) / 1_000_000
