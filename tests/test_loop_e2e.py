"""End-to-end: the agent fixes a failing test in a real repo, unattended.

This is the Phase 1 exit criterion. The provider is scripted so the test is deterministic
and makes no network call, but everything below it is real — the permission gate, the path
jail, the tools, a genuine pytest subprocess, and the trace record.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from support import ScriptedProvider, build_test_tools

from regin.config import Config, LoopConfig, ModelConfig, ToolsConfig
from regin.loop import AgentLoop
from regin.permissions import Permissions
from regin.schemas import Message, ModelResponse, Outcome, ToolCall, Usage
from regin.tools.shell import DEFAULT_ALLOWLIST

BUGGY_SOURCE = '''\
def add(a, b):
    """Add two numbers."""
    return a - b
'''

TEST_SOURCE = '''\
from calc import add


def test_add():
    assert add(2, 3) == 5
'''


@pytest.fixture
def broken_repo(tmp_path: Path) -> Path:
    (tmp_path / "calc.py").write_text(BUGGY_SOURCE, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(TEST_SOURCE, encoding="utf-8")
    return tmp_path


def _config() -> Config:
    return Config(
        model=ModelConfig(name="claude-sonnet-5", max_output_tokens=1024),
        loop=LoopConfig(max_turns=10, max_run_cost_usd=1.0),
        tools=ToolsConfig(bash_timeout_s=120, bash_allowlist=list(DEFAULT_ALLOWLIST)),
        permissions={
            "read_file": "allow",
            "glob": "allow",
            "grep": "allow",
            "edit_file": "allow",
            "write_file": "allow",
            "bash": "allow",
        },
    )


async def test_agent_fixes_a_failing_test_unattended(broken_repo: Path) -> None:
    # -B stops Python writing .pyc files, so none exists to go stale between the two runs.
    #
    # Without it this test fails on fast machines, and the reason is worth knowing: Python
    # invalidates cached bytecode on the source's size and mtime-truncated-to-seconds. This
    # fix ("a - b" -> "a + b") changes neither — identical byte length, and on CI the edit
    # lands in the same second as the first run. The second pytest then imports the stale
    # bytecode and still reports a failure, despite the source on disk being correct.
    #
    # This is a real hazard for any coding agent, not just this test: a same-size edit
    # applied quickly can make a passing fix look like it failed.
    pytest_command = f"{sys.executable} -B -m pytest -q"
    provider = ScriptedProvider(
        [
            _call("read_file", {"path": "test_calc.py"}),
            _call("read_file", {"path": "calc.py"}),
            _call("bash", {"command": pytest_command}),
            _call(
                "edit_file",
                {"path": "calc.py", "old": "return a - b", "new": "return a + b"},
            ),
            _call("bash", {"command": pytest_command}),
            ModelResponse(text="The test passes now.", usage=Usage(input_tokens=10)),
        ]
    )

    config = _config()
    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(broken_repo, config),
        permissions=Permissions(config.permissions),
        config=config,
    )

    record = await loop.run("Make the failing test in test_calc.py pass.", task_id="calc-fix")

    # The source was actually repaired on disk, not just claimed to be.
    assert "return a + b" in (broken_repo / "calc.py").read_text(encoding="utf-8")

    # The agent saw the failure before the fix and the pass after it.
    outputs = [r.content for results in provider.received for r in results]
    assert any("failed" in out or "assert" in out for out in outputs[:3])
    assert any("1 passed" in out for out in outputs[-2:])

    assert record.outcome is Outcome.RESOLVED
    assert record.turns == 6
    assert record.task_id == "calc-fix"
    assert record.cost_usd > 0
    assert record.finished_at is not None


async def test_a_failed_edit_is_recoverable_rather_than_fatal(broken_repo: Path) -> None:
    """A bad anchor is the single most common stall. It must come back as text the model
    can react to, not as an exception that kills the run."""
    provider = ScriptedProvider(
        [
            _call("edit_file", {"path": "calc.py", "old": "not present anywhere", "new": "x"}),
            _call(
                "edit_file",
                {"path": "calc.py", "old": "return a - b", "new": "return a + b"},
            ),
            ModelResponse(text="Recovered.", usage=Usage(input_tokens=1)),
        ]
    )

    config = _config()
    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(broken_repo, config),
        permissions=Permissions(config.permissions),
        config=config,
    )
    record = await loop.run("fix it")

    first_result = provider.received[0][0]
    assert first_result.is_error
    assert "re-read the file" in first_result.content
    assert record.outcome is Outcome.RESOLVED
    assert "return a + b" in (broken_repo / "calc.py").read_text(encoding="utf-8")


async def test_a_denied_tool_does_not_kill_the_run(broken_repo: Path) -> None:
    provider = ScriptedProvider(
        [
            _call("bash", {"command": "echo hello"}),
            ModelResponse(text="Understood, bash is unavailable.", usage=Usage(input_tokens=1)),
        ]
    )
    config = _config()
    config.permissions["bash"] = "deny"

    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(broken_repo, config),
        permissions=Permissions(config.permissions),
        config=config,
    )
    record = await loop.run("run something")

    assert provider.received[0][0].is_error
    assert "denied" in provider.received[0][0].content
    assert record.outcome is Outcome.RESOLVED


async def test_a_path_escape_is_reported_back_and_writes_nothing(broken_repo: Path) -> None:
    provider = ScriptedProvider(
        [
            _call("write_file", {"path": "../../pwned.txt", "content": "x"}),
            ModelResponse(text="Blocked.", usage=Usage(input_tokens=1)),
        ]
    )
    config = _config()
    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(broken_repo, config),
        permissions=Permissions(config.permissions),
        config=config,
    )
    await loop.run("escape the sandbox")

    assert provider.received[0][0].is_error
    assert "escapes the working directory" in provider.received[0][0].content
    assert not (broken_repo.parent.parent / "pwned.txt").exists()


def _call(name: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall(id=f"call_{name}_{len(arguments)}", name=name, arguments=arguments)],
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def test_scripted_provider_records_the_conversation() -> None:
    """Guard the harness itself — a silently broken fake would make every test vacuous."""
    provider = ScriptedProvider([ModelResponse(text="done")])
    assert provider.received == []
    assert isinstance(Message(role="user", content="x"), Message)
