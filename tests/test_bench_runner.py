"""Sweep orchestration: concurrency, the spend ceiling, and failure containment.

Workspace preparation is stubbed — these tests are about the runner's control flow, and
cloning real repositories would put the network in a unit test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from regin.bench import runner as runner_module
from regin.bench.runner import SweepConfig, run_sweep
from regin.bench.task import Task
from regin.bench.workspace import WorkspaceError
from regin.schemas import Outcome, RunRecord
from regin.trace import TraceStore


class FakeAgent:
    """Records what it was asked to do and returns a scripted outcome."""

    name = "fake"

    def __init__(
        self,
        cost: float = 0.10,
        outcome: Outcome = Outcome.RESOLVED,
        raises: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.cost = cost
        self.outcome = outcome
        self.raises = raises
        self.delay = delay
        self.seen: list[str] = []
        self.concurrent = 0
        self.peak_concurrent = 0

    async def run(self, task: Task, workdir: Path) -> RunRecord:
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.seen.append(task.instance_id)
            if self.raises:
                raise RuntimeError("agent exploded")
            return RunRecord(
                run_id=task.instance_id,
                task_id=task.instance_id,
                outcome=self.outcome,
                cost_usd=self.cost,
                patch="diff --git a/x b/x\n+fix\n",
            )
        finally:
            self.concurrent -= 1


@pytest.fixture(autouse=True)
def stub_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _prepare(repo: str, base_commit: str, dest: Path, cache_dir: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    async def _patch(workdir: Path) -> str:
        return "diff --git a/x b/x\n+fix\n"

    monkeypatch.setattr(runner_module, "prepare_workspace", _prepare)
    monkeypatch.setattr(runner_module, "extract_patch", _patch)


def _tasks(count: int) -> list[Task]:
    return [Task(instance_id=f"repo__issue-{i}", repo="a/b", base_commit="c") for i in range(count)]


def _config(tmp_path: Path, **overrides: object) -> SweepConfig:
    defaults: dict[str, object] = {
        "tasks_path": tmp_path / "tasks.jsonl",
        "output_dir": tmp_path / "out",
        "concurrency": 4,
        "max_sweep_cost_usd": 100.0,
        "per_task_estimate_usd": 0.50,
    }
    defaults.update(overrides)
    return SweepConfig(**defaults)  # pyright: ignore[reportArgumentType]


def _store(tmp_path: Path) -> TraceStore:
    return TraceStore(tmp_path / "bench.db", tmp_path / "records.jsonl")


async def test_runs_every_instance_and_writes_records(tmp_path: Path) -> None:
    agent = FakeAgent()
    result = await run_sweep(_tasks(5), agent, _config(tmp_path), _store(tmp_path))

    assert len(result.records) == 5
    assert sorted(agent.seen) == [f"repo__issue-{i}" for i in range(5)]
    assert (tmp_path / "records.jsonl").read_text(encoding="utf-8").count("\n") == 5


async def test_respects_the_concurrency_limit(tmp_path: Path) -> None:
    agent = FakeAgent(delay=0.02)
    await run_sweep(_tasks(12), agent, _config(tmp_path, concurrency=3), _store(tmp_path))
    assert agent.peak_concurrent <= 3


async def test_stops_dispatching_once_the_ceiling_is_reached(tmp_path: Path) -> None:
    """The plan calls an unbounded sweep the top financial risk — it must stop, not warn."""
    agent = FakeAgent(cost=1.0)
    config = _config(tmp_path, max_sweep_cost_usd=3.0, per_task_estimate_usd=1.0, concurrency=1)
    result = await run_sweep(_tasks(20), agent, config, _store(tmp_path))

    assert result.stopped_early is True
    assert len(result.records) == 3
    assert len(result.skipped) == 17
    assert sum(r.cost_usd for r in result.records) <= 3.0


async def test_one_crashing_instance_does_not_kill_the_sweep(tmp_path: Path) -> None:
    agent = FakeAgent(raises=True)
    result = await run_sweep(_tasks(3), agent, _config(tmp_path), _store(tmp_path))

    assert len(result.records) == 3
    assert all(r.outcome is Outcome.ERROR for r in result.records)
    assert "agent exploded" in (result.records[0].error or "")


async def test_a_checkout_failure_is_skipped_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An environment failure is not an agent failure and must not pollute the number."""

    async def _fail(repo: str, base_commit: str, dest: Path, cache_dir: Path) -> Path:
        raise WorkspaceError("clone failed")

    monkeypatch.setattr(runner_module, "prepare_workspace", _fail)
    result = await run_sweep(_tasks(3), FakeAgent(), _config(tmp_path), _store(tmp_path))

    assert result.records == []
    assert len(result.skipped) == 3
    assert result.resolve_rate == 0.0


async def test_a_claimed_fix_with_no_patch_is_downgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent's own claim of success is not evidence — an empty diff resolved nothing."""

    async def _empty(workdir: Path) -> str:
        return "   \n"

    monkeypatch.setattr(runner_module, "extract_patch", _empty)
    agent = FakeAgent(outcome=Outcome.RESOLVED)
    result = await run_sweep(_tasks(1), agent, _config(tmp_path), _store(tmp_path))

    assert result.records[0].outcome is Outcome.UNRESOLVED
    assert "empty patch" in (result.records[0].error or "")


async def test_resolve_rate_excludes_skipped_instances(tmp_path: Path) -> None:
    """Counting never-attempted instances as failures would reward a crashing harness."""
    agent = FakeAgent(cost=1.0, outcome=Outcome.RESOLVED)
    config = _config(tmp_path, max_sweep_cost_usd=2.0, per_task_estimate_usd=1.0, concurrency=1)
    result = await run_sweep(_tasks(10), agent, config, _store(tmp_path))

    assert len(result.records) == 2
    assert result.resolve_rate == 1.0


async def test_records_carry_the_agent_name_for_the_scorecard(tmp_path: Path) -> None:
    result = await run_sweep(_tasks(2), FakeAgent(), _config(tmp_path), _store(tmp_path))
    assert {r.agent for r in result.records} == {"fake"}
