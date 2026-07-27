"""The spend ceiling. The plan names an unbounded sweep as the top financial risk."""

from __future__ import annotations

import asyncio

from regin.bench.budget import Budget


async def test_reserves_until_the_ceiling_is_reached() -> None:
    budget = Budget(ceiling_usd=1.0, per_task_estimate_usd=0.25)
    assert [await budget.reserve() for _ in range(4)] == [True, True, True, True]
    assert await budget.reserve() is False


async def test_settling_below_estimate_frees_headroom() -> None:
    """Tasks that come in under estimate must not permanently consume the reservation."""
    budget = Budget(ceiling_usd=1.0, per_task_estimate_usd=0.50)
    assert await budget.reserve() is True
    await budget.settle(0.10)
    assert budget.spent_usd == 0.10
    assert budget.remaining_usd == 0.90


async def test_settling_above_estimate_tightens_the_remaining_budget() -> None:
    budget = Budget(ceiling_usd=1.0, per_task_estimate_usd=0.10)
    assert await budget.reserve() is True
    await budget.settle(0.95)
    assert await budget.reserve() is False


async def test_concurrent_reservations_cannot_oversubscribe() -> None:
    """The reason reservation exists: with N tasks in flight, post-hoc accounting can
    overshoot by N times the per-task cost before anything notices."""
    budget = Budget(ceiling_usd=1.0, per_task_estimate_usd=0.25)
    granted = await asyncio.gather(*(budget.reserve() for _ in range(50)))
    assert sum(granted) == 4


async def test_reports_the_overrun_for_the_operator() -> None:
    budget = Budget(ceiling_usd=2.0, per_task_estimate_usd=1.0)
    await budget.reserve()
    await budget.settle(1.75)
    exceeded = budget.exceeded()
    assert exceeded.ceiling_usd == 2.0
    assert "1.75" in str(exceeded)
