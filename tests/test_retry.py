"""Transient-failure retry.

The distinction that matters: a 429 is worth retrying, a 400 never is. Retrying a malformed
request burns time and money to arrive at the same error.
"""

from __future__ import annotations

from typing import Any

import pytest

from regin.providers.retry import RetryingProvider, is_transient, retry_after_seconds
from regin.schemas import Message, ModelResponse, Usage


class FakeStatusError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = type("R", (), {"headers": headers or {}})()


class FlakyProvider:
    """Fails a set number of times, then succeeds."""

    name = "flaky"

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self._remaining = failures
        self._error = error or FakeStatusError(429)
        self.calls = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_output_tokens: int,
        effort: str = "high",
    ) -> ModelResponse:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return ModelResponse(text="ok", usage=Usage(input_tokens=1))


def _messages() -> list[Message]:
    return [Message(role="user", content="hi")]


async def _complete(provider: RetryingProvider) -> ModelResponse:
    return await provider.complete(
        messages=_messages(), tools=[], model="claude-sonnet-5", max_output_tokens=100
    )


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff correctness is asserted separately; tests must not actually wait."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("regin.providers.retry.asyncio.sleep", _instant)


async def test_succeeds_without_retrying_when_the_call_works() -> None:
    inner = FlakyProvider(failures=0)
    assert (await _complete(RetryingProvider(inner))).text == "ok"
    assert inner.calls == 1


async def test_retries_a_rate_limit_until_it_succeeds() -> None:
    inner = FlakyProvider(failures=2)
    result = await _complete(RetryingProvider(inner, max_attempts=4))

    assert result.text == "ok"
    assert inner.calls == 3


async def test_gives_up_after_the_attempt_limit() -> None:
    inner = FlakyProvider(failures=10)
    with pytest.raises(FakeStatusError):
        await _complete(RetryingProvider(inner, max_attempts=3))

    assert inner.calls == 3


async def test_does_not_retry_a_malformed_request() -> None:
    """A 400 will be a 400 again; retrying only wastes the sweep's budget."""
    inner = FlakyProvider(failures=10, error=FakeStatusError(400))
    with pytest.raises(FakeStatusError):
        await _complete(RetryingProvider(inner, max_attempts=5))

    assert inner.calls == 1


async def test_does_not_retry_an_authentication_failure() -> None:
    inner = FlakyProvider(failures=10, error=FakeStatusError(401))
    with pytest.raises(FakeStatusError):
        await _complete(RetryingProvider(inner))

    assert inner.calls == 1


async def test_exposes_the_wrapped_provider_name() -> None:
    assert RetryingProvider(FlakyProvider(0)).name == "flaky"


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_transient_statuses_are_retryable(status: int) -> None:
    assert is_transient(FakeStatusError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status: int) -> None:
    assert is_transient(FakeStatusError(status)) is False


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError()])
def test_transport_failures_are_retryable(error: Exception) -> None:
    assert is_transient(error) is True


def test_sdk_error_classes_are_recognised_by_name() -> None:
    """Matching on the class name survives SDK error-message rewording."""
    rate_limit = type("RateLimitError", (Exception,), {})()
    assert is_transient(rate_limit) is True


def test_an_unrecognised_error_is_not_retried() -> None:
    assert is_transient(ValueError("something else")) is False


def test_reads_a_server_supplied_retry_after() -> None:
    assert retry_after_seconds(FakeStatusError(429, {"retry-after": "12"})) == 12.0


def test_ignores_an_unparseable_retry_after() -> None:
    assert retry_after_seconds(FakeStatusError(429, {"retry-after": "soon"})) is None


def test_absent_headers_yield_no_suggestion() -> None:
    assert retry_after_seconds(ValueError("no response attached")) is None


async def test_honours_retry_after_over_computed_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("regin.providers.retry.asyncio.sleep", _record)

    inner = FlakyProvider(failures=1, error=FakeStatusError(429, {"retry-after": "7"}))
    await _complete(RetryingProvider(inner, max_delay_s=30))

    assert slept == [7.0]


async def test_backoff_is_jittered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without jitter, concurrent instances rate-limited together retry in lockstep and
    rate-limit together again."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("regin.providers.retry.asyncio.sleep", _record)

    inner = FlakyProvider(failures=3)
    await _complete(RetryingProvider(inner, max_attempts=5, base_delay_s=1.0))

    assert len(slept) == 3
    assert all(delay > 0 for delay in slept)
    assert len(set(slept)) > 1  # jitter makes the delays differ


async def test_backoff_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("regin.providers.retry.asyncio.sleep", _record)

    inner = FlakyProvider(failures=4)
    await _complete(RetryingProvider(inner, max_attempts=6, base_delay_s=100, max_delay_s=5))

    assert all(delay <= 5 for delay in slept)
