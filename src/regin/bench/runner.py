"""Sweep execution: fan out over instances, bounded by concurrency and by spend.

Records are written as each task finishes rather than at the end. A sweep that dies at
instance 400 must still leave 399 scoreable records behind.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from regin.bench.budget import Budget
from regin.bench.task import Task
from regin.bench.workspace import WorkspaceError, extract_patch, prepare_workspace
from regin.schemas import Outcome, RunRecord
from regin.trace import TraceStore


class BenchAgent(Protocol):
    """What a competitor must implement to be scored on the same footing.

    regin, Claude Code, and Aider all reduce to this: given a task and a checkout, mutate
    the checkout and report what it cost.
    """

    name: str

    async def run(self, task: Task, workdir: Path) -> RunRecord: ...


@dataclass
class SweepConfig:
    tasks_path: Path
    output_dir: Path
    concurrency: int = 4
    max_sweep_cost_usd: float = 25.0
    per_task_estimate_usd: float = 0.50
    limit: int | None = None


@dataclass
class SweepResult:
    records: list[RunRecord]
    skipped: list[str]
    stopped_early: bool
    wall_clock_s: float

    @property
    def resolve_rate(self) -> float:
        """Share of *attempted* instances that resolved.

        Skipped instances are excluded from the denominator: they were never attempted, so
        counting them as failures would understate the agent and reward a crashing harness.
        """
        if not self.records:
            return 0.0
        resolved = sum(1 for r in self.records if r.outcome is Outcome.RESOLVED)
        return resolved / len(self.records)


async def run_sweep(
    tasks: list[Task],
    agent: BenchAgent,
    config: SweepConfig,
    store: TraceStore,
) -> SweepResult:
    started = time.monotonic()
    budget = Budget(config.max_sweep_cost_usd, config.per_task_estimate_usd)
    semaphore = asyncio.Semaphore(config.concurrency)
    cache_dir = config.output_dir / "repo-cache"

    records: list[RunRecord] = []
    skipped: list[str] = []
    stopped = asyncio.Event()

    async def attempt(task: Task) -> None:
        if stopped.is_set():
            skipped.append(task.instance_id)
            return

        # Reserved before the semaphore so a full queue cannot commit spend it lacks.
        if not await budget.reserve():
            stopped.set()
            skipped.append(task.instance_id)
            return

        async with semaphore:
            record = await _run_one(task, agent, config, cache_dir)

        await budget.settle(record.cost_usd)
        if record.outcome is Outcome.ERROR and record.error and "workspace" in record.error:
            skipped.append(task.instance_id)
            return

        records.append(record)
        store.write(record)

    await asyncio.gather(*(attempt(task) for task in tasks))

    return SweepResult(
        records=records,
        skipped=skipped,
        stopped_early=stopped.is_set(),
        wall_clock_s=time.monotonic() - started,
    )


async def _run_one(
    task: Task,
    agent: BenchAgent,
    config: SweepConfig,
    cache_dir: Path,
) -> RunRecord:
    workdir = config.output_dir / "instances" / task.instance_id
    base = RunRecord(
        run_id=f"{agent.name}-{task.instance_id}",
        task_id=task.instance_id,
        agent=agent.name,
    )

    try:
        await prepare_workspace(task.repo, task.base_commit, workdir, cache_dir)
    except WorkspaceError as exc:
        base.outcome = Outcome.ERROR
        base.error = f"workspace: {exc}"
        return base

    try:
        record = await agent.run(task, workdir)
    except Exception as exc:
        # One crashed instance must not take the sweep with it.
        base.outcome = Outcome.ERROR
        base.error = f"{type(exc).__name__}: {exc}"
        return base

    record.agent = agent.name
    record.task_id = task.instance_id

    try:
        record.patch = await extract_patch(workdir)
    except WorkspaceError as exc:
        record.error = f"patch extraction failed: {exc}"

    # The agent's own claim of success is not evidence. `evalgate` decides whether the
    # patch resolves the instance; the loop only reports how the run terminated.
    if record.outcome is Outcome.RESOLVED and not record.patch.strip():
        record.outcome = Outcome.UNRESOLVED
        record.error = "agent reported completion but produced an empty patch"

    return record
