"""Core data shapes shared by the loop, the tools, and the trace layer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ERROR = "error"
    BUDGET_EXCEEDED = "budget_exceeded"
    TURN_LIMIT = "turn_limit"
    DENIED = "denied"


class Usage(BaseModel):
    """Token counts as reported by the provider. Never estimated."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)

    # Raw provider content blocks for an assistant turn, replayed verbatim on the next
    # request. Thinking blocks are rejected if they are reconstructed rather than echoed.
    provider_blocks: list[dict[str, Any]] = Field(default_factory=list)


class ModelResponse(BaseModel):
    """One assistant turn, normalised across providers."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = ""
    latency_ms: int = 0
    provider_blocks: list[dict[str, Any]] = Field(default_factory=list)


class RunRecord(BaseModel):
    """The contract with evalgate. One JSONL line per agent run.

    `config_hash` is what makes A/B comparison honest: it pins the exact prompt set,
    model, and tool configuration a result came from.
    """

    run_id: str
    task_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    agent: str = "regin"
    model: str = ""
    config_hash: str = ""

    outcome: Outcome = Outcome.ERROR
    turns: int = 0
    patch: str = ""

    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: int = 0

    error: str | None = None

    def to_jsonl(self) -> str:
        return self.model_dump_json() + "\n"


def config_hash(payload: dict[str, Any]) -> str:
    """Stable hash of a run configuration. Sorted keys so ordering never changes it."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
