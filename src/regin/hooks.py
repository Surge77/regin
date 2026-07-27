"""Pre- and post-tool hooks.

Phase 4.2. A hook observes or vetoes a tool call: auto-format after an edit, block writes
to a protected path, log every command to an audit trail.

Two rules keep this from becoming a second, unreviewed control flow:

1. **Hooks cannot rewrite a tool call.** They observe, and a pre-hook may veto. Allowing
   mutation would mean the call the model made and the call that ran could differ, and every
   trace record would be a guess.
2. **A hook that raises is a denial, not a crash.** A broken hook must not take the run with
   it, and silently ignoring its failure would defeat the point of having a gate.

Hooks run *inside* the permission layer, never around it. A hook cannot approve something
permissions denied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from regin.schemas import ToolCall, ToolResult


@dataclass(frozen=True)
class HookVeto(Exception):
    """A pre-hook refused the call. Recoverable — reported back to the model as text."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@runtime_checkable
class PreToolHook(Protocol):
    name: str

    async def before(self, call: ToolCall) -> None:
        """Raise HookVeto to block the call. Any other exception is treated as a veto."""
        ...


@runtime_checkable
class PostToolHook(Protocol):
    name: str

    async def after(self, call: ToolCall, result: ToolResult) -> None:
        """Observe a completed call. Cannot change the result the model sees."""
        ...


class HookRegistry:
    def __init__(self) -> None:
        self._pre: list[PreToolHook] = []
        self._post: list[PostToolHook] = []

    def add_pre(self, hook: PreToolHook) -> None:
        self._pre.append(hook)

    def add_post(self, hook: PostToolHook) -> None:
        self._post.append(hook)

    @property
    def is_empty(self) -> bool:
        return not self._pre and not self._post

    async def before(self, call: ToolCall) -> None:
        """Run every pre-hook. The first veto wins and the rest do not run."""
        for hook in self._pre:
            try:
                await hook.before(call)
            except HookVeto:
                raise
            except Exception as exc:
                # Fail closed. A hook exists to gate something; treating its failure as
                # approval would make it worse than no hook at all.
                raise HookVeto(f"hook {hook.name!r} failed: {type(exc).__name__}: {exc}") from exc

    async def after(self, call: ToolCall, result: ToolResult) -> None:
        """Run every post-hook. A failure here is swallowed — the tool already ran, and the
        model's result must not change because a formatter crashed."""
        for hook in self._post:
            try:
                await hook.after(call, result)
            except Exception:  # noqa: S110 - deliberate; see docstring
                continue


class BlockPathsHook:
    """Refuse writes to paths matching any of the given substrings.

    The motivating case is a repository that keeps credentials or generated artifacts in
    tree: the model has no way to know they are off limits, and a denial with a reason is
    something it can work around.
    """

    name = "block-paths"

    def __init__(
        self,
        blocked: tuple[str, ...],
        tools: tuple[str, ...] = ("write_file", "edit_file"),
    ) -> None:
        self._blocked = blocked
        self._tools = tools

    async def before(self, call: ToolCall) -> None:
        if call.name not in self._tools:
            return
        path = str(call.arguments.get("path", ""))
        for fragment in self._blocked:
            if fragment in path:
                raise HookVeto(f"writes to paths containing {fragment!r} are blocked by policy")


class AuditLogHook:
    """Append every tool call and its outcome to a list. The audit trail a sweep needs when
    a run does something surprising and the transcript alone does not explain it."""

    name = "audit-log"

    def __init__(self) -> None:
        self.entries: list[str] = []

    async def after(self, call: ToolCall, result: ToolResult) -> None:
        status = "error" if result.is_error else "ok"
        self.entries.append(f"{call.name} {status}")
