"""The table the project exists to produce.

Reports median cost rather than mean: cost per task is long-tailed — a handful of runs
that hit the turn limit dominate a mean and make two configurations look different when
their typical behaviour is identical. p95 latency for the same reason, in the other
direction: the tail is exactly what matters for wall-clock planning.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from rich.table import Table

from regin.schemas import Outcome, RunRecord


@dataclass
class AgentScore:
    agent: str
    config_hash: str
    attempted: int
    resolved: int
    median_cost_usd: float
    total_cost_usd: float
    p95_latency_s: float
    mean_turns: float
    cost_reported: bool

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.attempted if self.attempted else 0.0


def load_records(path: Path) -> list[RunRecord]:
    """Read the JSONL contract. Malformed lines are skipped, not fatal — a truncated
    final line from an interrupted sweep must not make the rest unreadable."""
    records: list[RunRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                records.append(RunRecord.model_validate_json(line))
            except (ValueError, json.JSONDecodeError):
                continue
    return records


def score(records: list[RunRecord], resolved_ids: set[str] | None = None) -> list[AgentScore]:
    """Aggregate per (agent, config_hash).

    `resolved_ids` is the scorer's verdict from evalgate. When absent the agent's own
    terminal outcome is used, which measures whether the loop finished cleanly — useful
    for smoke runs, but it is not a resolve-rate and should not be published as one.
    """
    grouped: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.agent, record.config_hash)].append(record)

    scores: list[AgentScore] = []
    for (agent, config), group in sorted(grouped.items()):
        if resolved_ids is None:
            resolved = sum(1 for r in group if r.outcome is Outcome.RESOLVED)
        else:
            resolved = sum(1 for r in group if r.task_id in resolved_ids)

        costs = [r.cost_usd for r in group]
        latencies = sorted(r.latency_ms / 1000 for r in group)

        scores.append(
            AgentScore(
                agent=agent,
                config_hash=config,
                attempted=len(group),
                resolved=resolved,
                median_cost_usd=median(costs) if costs else 0.0,
                total_cost_usd=sum(costs),
                p95_latency_s=_percentile(latencies, 0.95),
                mean_turns=sum(r.turns for r in group) / len(group),
                cost_reported=any(c > 0 for c in costs),
            )
        )
    return scores


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: the smallest value at or below which `fraction` of the
    samples fall.

    Spelled out because the definition genuinely changes the published figure at small N —
    with 20 samples, p95 is the 19th, so a single outlier does not move it. A benchmark
    table that cannot be reproduced from its own stated definition is worthless.
    """
    if not sorted_values:
        return 0.0
    rank = max(1, min(len(sorted_values), math.ceil(fraction * len(sorted_values))))
    return sorted_values[rank - 1]


def render(scores: list[AgentScore]) -> Table:
    table = Table(title="regin benchmark scorecard")
    table.add_column("agent")
    table.add_column("config", style="dim")
    table.add_column("resolve", justify="right")
    table.add_column("n", justify="right")
    table.add_column("median $", justify="right")
    table.add_column("total $", justify="right")
    table.add_column("p95 latency", justify="right")
    table.add_column("mean turns", justify="right")

    for entry in sorted(scores, key=lambda s: s.resolve_rate, reverse=True):
        cost = f"${entry.median_cost_usd:.3f}" if entry.cost_reported else "n/a"
        total = f"${entry.total_cost_usd:.2f}" if entry.cost_reported else "n/a"
        table.add_row(
            entry.agent,
            entry.config_hash[:8] or "—",
            f"{entry.resolve_rate:.1%}",
            str(entry.attempted),
            cost,
            total,
            f"{entry.p95_latency_s:.0f}s",
            f"{entry.mean_turns:.1f}",
        )
    return table


def to_markdown(scores: list[AgentScore]) -> str:
    """The README table. Publishing the number is the point of Phase 2."""
    lines = [
        "| agent | config | resolve-rate | n | median $/task | p95 latency | mean turns |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for entry in sorted(scores, key=lambda s: s.resolve_rate, reverse=True):
        cost = f"${entry.median_cost_usd:.3f}" if entry.cost_reported else "n/a"
        lines.append(
            f"| {entry.agent} | `{entry.config_hash[:8] or '—'}` | {entry.resolve_rate:.1%} | "
            f"{entry.attempted} | {cost} | {entry.p95_latency_s:.0f}s | {entry.mean_turns:.1f} |"
        )
    return "\n".join(lines)
