"""Pre- and post-tool hooks.

The rule that matters: hooks run inside the permission gate. A hook can veto, never approve.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import ScriptedProvider, build_test_tools

from regin.config import Config, ModelConfig
from regin.hooks import AuditLogHook, BlockPathsHook, HookRegistry, HookVeto
from regin.loop import AgentLoop
from regin.permissions import Permissions
from regin.schemas import ModelResponse, ToolCall, ToolResult, Usage


class RecordingPreHook:
    name = "recorder"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def before(self, call: ToolCall) -> None:
        self.seen.append(call.name)


class ExplodingPreHook:
    name = "exploding"

    async def before(self, call: ToolCall) -> None:
        raise RuntimeError("hook is broken")


class ExplodingPostHook:
    name = "exploding-post"

    async def after(self, call: ToolCall, result: ToolResult) -> None:
        raise RuntimeError("formatter crashed")


def _call(name: str = "write_file", **arguments: object) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments or {"path": "x.py", "content": "y"})


async def test_a_pre_hook_observes_every_call() -> None:
    hooks = HookRegistry()
    recorder = RecordingPreHook()
    hooks.add_pre(recorder)

    await hooks.before(_call("bash", command="ls"))

    assert recorder.seen == ["bash"]


async def test_a_veto_blocks_the_call() -> None:
    hooks = HookRegistry()
    hooks.add_pre(BlockPathsHook(blocked=(".env",)))

    with pytest.raises(HookVeto, match="blocked by policy"):
        await hooks.before(_call(path=".env.production", content="x"))


async def test_a_block_hook_ignores_unrelated_tools() -> None:
    hooks = HookRegistry()
    hooks.add_pre(BlockPathsHook(blocked=(".env",)))

    await hooks.before(_call("read_file", path=".env"))


async def test_a_broken_pre_hook_fails_closed() -> None:
    """A hook exists to gate something; treating its failure as approval is worse than
    having no hook at all."""
    hooks = HookRegistry()
    hooks.add_pre(ExplodingPreHook())

    with pytest.raises(HookVeto, match="hook 'exploding' failed"):
        await hooks.before(_call())


async def test_the_first_veto_stops_later_hooks() -> None:
    hooks = HookRegistry()
    recorder = RecordingPreHook()
    hooks.add_pre(BlockPathsHook(blocked=("secret",)))
    hooks.add_pre(recorder)

    with pytest.raises(HookVeto):
        await hooks.before(_call(path="secret.py", content="x"))

    assert recorder.seen == []


async def test_a_post_hook_sees_the_result() -> None:
    hooks = HookRegistry()
    audit = AuditLogHook()
    hooks.add_post(audit)

    await hooks.after(_call("bash"), ToolResult(call_id="c1", content="ok"))
    await hooks.after(_call("bash"), ToolResult(call_id="c1", content="no", is_error=True))

    assert audit.entries == ["bash ok", "bash error"]


async def test_a_broken_post_hook_does_not_change_the_result() -> None:
    """The tool already ran; the model's result must not change because a formatter died."""
    hooks = HookRegistry()
    hooks.add_post(ExplodingPostHook())

    await hooks.after(_call(), ToolResult(call_id="c1", content="fine"))


def test_an_empty_registry_reports_itself_as_such() -> None:
    hooks = HookRegistry()
    assert hooks.is_empty is True
    hooks.add_pre(RecordingPreHook())
    assert hooks.is_empty is False


async def test_a_veto_reaches_the_model_as_a_recoverable_error(tmp_path: Path) -> None:
    """End to end: the run continues and the model is told why."""
    config = Config(model=ModelConfig(name="claude-sonnet-5"), permissions={"write_file": "allow"})
    hooks = HookRegistry()
    hooks.add_pre(BlockPathsHook(blocked=(".env",)))

    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": ".env", "content": "KEY=leaked"},
                    )
                ],
                usage=Usage(input_tokens=1),
            ),
            ModelResponse(text="Understood, that path is protected."),
        ]
    )

    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(tmp_path, config),
        permissions=Permissions(config.permissions),
        config=config,
        hooks=hooks,
    )
    await loop.run("write the env file")

    assert provider.received[0][0].is_error
    assert "blocked by policy" in provider.received[0][0].content
    assert not (tmp_path / ".env").exists()


async def test_hooks_cannot_approve_what_permissions_denied(tmp_path: Path) -> None:
    """Hooks run inside the gate, so a permissive hook grants nothing."""
    config = Config(model=ModelConfig(name="claude-sonnet-5"), permissions={"write_file": "deny"})
    hooks = HookRegistry()
    hooks.add_pre(RecordingPreHook())

    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(id="c1", name="write_file", arguments={"path": "a", "content": "b"})
                ],
                usage=Usage(input_tokens=1),
            ),
            ModelResponse(text="ok"),
        ]
    )

    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(tmp_path, config),
        permissions=Permissions(config.permissions),
        config=config,
        hooks=hooks,
    )
    await loop.run("write a file")

    assert "denied by configuration" in provider.received[0][0].content
    assert not (tmp_path / "a").exists()
