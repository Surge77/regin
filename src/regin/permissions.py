"""Permission gate. Every tool call passes through here — including in tests."""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class Prompter(Protocol):
    def confirm(self, tool_name: str, summary: str) -> bool: ...


class DenyAll:
    """Default prompter for tests: refuses anything that would need a human."""

    def confirm(self, tool_name: str, summary: str) -> bool:
        return False


class AutoApprove:
    """Benchmark mode. There is no human in the loop at 500 tasks."""

    def confirm(self, tool_name: str, summary: str) -> bool:
        return True


class ConsolePrompter:
    """Interactive approval. Anything unparseable is treated as a refusal — the safe
    default when a human is present but distracted."""

    def confirm(self, tool_name: str, summary: str) -> bool:
        try:
            answer = input(f"allow {summary}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in {"y", "yes"}


class PermissionError_(RuntimeError):
    """Raised when a tool call is refused. Recoverable — fed back to the model."""


class Permissions:
    def __init__(
        self,
        rules: dict[str, str] | None = None,
        prompter: Prompter | None = None,
        auto_approve: bool = False,
    ) -> None:
        self._rules = {k: Decision(v) for k, v in (rules or {}).items()}
        self._prompter: Prompter = AutoApprove() if auto_approve else (prompter or DenyAll())

    def check(self, tool_name: str, summary: str) -> None:
        """Raise PermissionError_ if the call is not permitted."""
        decision = self._rules.get(tool_name, Decision.ASK)

        if decision is Decision.ALLOW:
            return
        if decision is Decision.DENY:
            raise PermissionError_(f"tool '{tool_name}' is denied by configuration")
        if not self._prompter.confirm(tool_name, summary):
            raise PermissionError_(f"user declined tool '{tool_name}'")
