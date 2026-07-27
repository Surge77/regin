"""The spend ceiling for a sweep.

A three-agent sweep over hundreds of instances is real money, and the failure mode is not
gradual: a prompt regression that makes every run hit the turn limit multiplies the bill
by the turn cap. The ceiling is therefore checked *before* dispatching each task, not
reported afterwards.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetExceeded:
    """Returned rather than raised: an exhausted budget is an expected end state for a
    sweep, and the records gathered before it must still be written."""

    spent_usd: float
    ceiling_usd: float

    def __str__(self) -> str:
        return f"sweep budget exhausted: ${self.spent_usd:.2f} of ${self.ceiling_usd:.2f}"


class Budget:
    """Tracks spend across concurrent tasks.

    Reserving before dispatch rather than accounting after completion is what makes the
    ceiling meaningful under concurrency: with N tasks in flight, post-hoc accounting can
    overshoot by N times the per-task cost before anything notices.
    """

    def __init__(self, ceiling_usd: float, per_task_estimate_usd: float) -> None:
        self._ceiling = ceiling_usd
        self._estimate = per_task_estimate_usd
        self._spent = 0.0
        self._reserved = 0.0
        self._lock = asyncio.Lock()

    @property
    def spent_usd(self) -> float:
        return self._spent

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self._ceiling - self._spent - self._reserved)

    async def reserve(self) -> bool:
        """Claim headroom for one task. False means the sweep must stop dispatching."""
        async with self._lock:
            if self._spent + self._reserved + self._estimate > self._ceiling:
                return False
            self._reserved += self._estimate
            return True

    async def settle(self, actual_usd: float) -> None:
        """Replace a reservation with what the task actually cost."""
        async with self._lock:
            self._reserved = max(0.0, self._reserved - self._estimate)
            self._spent += actual_usd

    def exceeded(self) -> BudgetExceeded:
        return BudgetExceeded(spent_usd=self._spent, ceiling_usd=self._ceiling)
