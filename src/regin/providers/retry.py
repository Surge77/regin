"""Transient-failure retry around the provider.

Phase 3.5. A sweep is long and providers rate-limit; without retry a 429 at instance 200
scores as an agent failure, which is a lie about the agent and a waste of the spend.

Wrapping the provider rather than putting retry inside the loop keeps `loop.py` about
control flow and means every consumer — the agent, the planner, the compactor — inherits it.

Only transient failures are retried. A 400 means the request is malformed and will be
malformed again; retrying it burns time and money to arrive at the same error.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from regin.providers.base import Provider
from regin.schemas import Message, ModelResponse

# Retried: rate limits, overload, gateway failures, and transport errors.
TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class RetryingProvider:
    """Decorates a provider with bounded exponential backoff and jitter."""

    def __init__(
        self,
        inner: Provider,
        max_attempts: int = 4,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
    ) -> None:
        self._inner = inner
        self._max_attempts = max_attempts
        self._base_delay = base_delay_s
        self._max_delay = max_delay_s
        self.attempts_used = 0

    @property
    def name(self) -> str:
        return getattr(self._inner, "name", "unknown")

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ) -> ModelResponse:
        last: Exception | None = None

        for attempt in range(self._max_attempts):
            self.attempts_used = attempt + 1
            try:
                return await self._inner.complete(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_output_tokens=max_output_tokens,
                    effort=effort,
                )
            except Exception as exc:
                if not is_transient(exc) or attempt == self._max_attempts - 1:
                    raise
                last = exc
                await asyncio.sleep(self._backoff(attempt, exc))

        raise last or RuntimeError("retry loop exited without a result")

    def _backoff(self, attempt: int, exc: Exception) -> float:
        """Honour a server-supplied `retry-after` when present; otherwise back off.

        Jitter matters under the sweep's concurrency: without it, N in-flight instances
        rate-limited at the same moment retry in lockstep and rate-limit again together.
        """
        suggested = retry_after_seconds(exc)
        if suggested is not None:
            return min(suggested, self._max_delay)

        delay = min(self._base_delay * (2**attempt), self._max_delay)
        return delay * (0.5 + random.random() / 2)


def is_transient(exc: Exception) -> bool:
    """Whether retrying could plausibly succeed.

    Checks the structured status code first and only falls back to the message text, since
    matching on prose breaks whenever the SDK rewords an error.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in TRANSIENT_STATUS

    if isinstance(exc, TimeoutError | ConnectionError):
        return True

    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }


def retry_after_seconds(exc: Exception) -> float | None:
    """Read `retry-after` from the response headers if the SDK exposed them."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
