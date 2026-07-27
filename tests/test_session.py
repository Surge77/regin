"""Session persistence and resume.

The guard that matters: resuming under a different configuration would blend two
configurations, and any number derived from the result would belong to neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import ScriptedProvider, build_test_tools

from regin.config import Config, LoopConfig, ModelConfig
from regin.loop import AgentLoop
from regin.permissions import Permissions
from regin.schemas import Message, ModelResponse, Outcome, RunRecord, ToolCall, Usage
from regin.session import ConfigMismatchError, Session, SessionStore


def _record(outcome: Outcome = Outcome.TURN_LIMIT, run_id: str = "run1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id="t1",
        config_hash="abc12345",
        outcome=outcome,
        turns=40,
        cost_usd=1.25,
    )


def _messages() -> list[Message]:
    return [
        Message(role="system", content="system"),
        Message(role="user", content="fix the parser"),
        Message(role="assistant", content="I read parser.py"),
    ]


def test_round_trips_a_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.save(Session.from_run(_record(), "fix the parser", _messages()))

    loaded = store.load("run1")

    assert loaded.task == "fix the parser"
    assert loaded.turns == 40
    assert len(loaded.messages) == 3
    assert loaded.messages[2].content == "I read parser.py"


@pytest.mark.parametrize(
    ("outcome", "resumable"),
    [
        (Outcome.TURN_LIMIT, True),
        (Outcome.BUDGET_EXCEEDED, True),
        (Outcome.RESOLVED, False),
        (Outcome.ERROR, False),
    ],
)
def test_only_limit_stops_are_worth_resuming(outcome: Outcome, resumable: bool) -> None:
    """A resolved run has nothing left to do; a provider error left no useful state."""
    session = Session.from_run(_record(outcome=outcome), "task", _messages())
    assert session.is_resumable is resumable


def test_resuming_restores_the_conversation(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = Session.from_run(_record(), "fix the parser", _messages())

    resumed = store.resume_messages(session, "abc12345")

    assert resumed[:3] == _messages()
    assert "[RESUMED]" in resumed[-1].content


def test_the_resume_marker_tells_the_model_not_to_repeat_work(tmp_path: Path) -> None:
    """Without it the conversation looks merely paused and the model re-runs its commands."""
    store = SessionStore(tmp_path)
    resumed = store.resume_messages(Session.from_run(_record(), "t", _messages()), "abc12345")

    assert "Do not repeat work already" in resumed[-1].content


def test_refuses_to_resume_under_a_different_config(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = Session.from_run(_record(), "task", _messages())

    with pytest.raises(ConfigMismatchError, match="attributable to neither"):
        store.resume_messages(session, "different")


def test_lists_sessions_newest_first(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    for index in range(3):
        session = Session.from_run(_record(run_id=f"run{index}"), f"task {index}", [])
        session.saved_at = session.saved_at.replace(microsecond=index * 1000)
        store.save(session)

    assert [s.run_id for s in store.list()] == ["run2", "run1", "run0"]


def test_a_corrupt_session_does_not_hide_the_rest(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.save(Session.from_run(_record(), "good task", []))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    listed = store.list()

    assert len(listed) == 1
    assert listed[0].task == "good task"


def test_latest_returns_none_when_empty(tmp_path: Path) -> None:
    assert SessionStore(tmp_path).latest() is None


def test_loading_an_unknown_session_says_so(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no session"):
        SessionStore(tmp_path).load("nope")


async def test_a_resumed_run_continues_rather_than_restarting(tmp_path: Path) -> None:
    """End to end: the loop picks up the stored history instead of a fresh prompt."""
    config = Config(
        model=ModelConfig(name="claude-sonnet-5"),
        loop=LoopConfig(max_turns=5),
        permissions={"read_file": "allow"},
    )
    provider = ScriptedProvider([ModelResponse(text="finished the remaining work")])

    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(tmp_path, config),
        permissions=Permissions(config.permissions),
        config=config,
    )
    prior = [*_messages(), Message(role="assistant", content="ruled out the lexer")]

    record = await loop.run("fix the parser", resume_from=prior)

    assert record.outcome is Outcome.RESOLVED
    assert provider.last_messages[:4] == prior
    assert "ruled out the lexer" in provider.last_messages[3].content


async def test_the_loop_exposes_its_conversation_for_saving(tmp_path: Path) -> None:
    config = Config(model=ModelConfig(name="claude-sonnet-5"), loop=LoopConfig(max_turns=2))
    searching = ModelResponse(
        tool_calls=[ToolCall(id="c1", name="glob", arguments={"pattern": "*.py"})],
        usage=Usage(input_tokens=1),
    )
    provider = ScriptedProvider([searching] * 5)

    loop = AgentLoop(
        provider=provider,
        tools=build_test_tools(tmp_path, config),
        permissions=Permissions({"glob": "allow"}),
        config=config,
    )
    record = await loop.run("find things")

    assert record.outcome is Outcome.TURN_LIMIT
    assert len(loop.messages) > 2

    session = Session.from_run(record, "find things", loop.messages)
    assert session.is_resumable
    assert SessionStore(tmp_path / "sessions").save(session).exists()
