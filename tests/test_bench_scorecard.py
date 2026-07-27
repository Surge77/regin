"""Scorecard aggregation — the table the project exists to produce."""

from __future__ import annotations

from pathlib import Path

from regin.bench.scorecard import load_records, score, to_markdown
from regin.schemas import Outcome, RunRecord


def _record(
    agent: str,
    task_id: str,
    outcome: Outcome = Outcome.RESOLVED,
    cost: float = 0.10,
    latency_ms: int = 1000,
    turns: int = 5,
    config: str = "abc123",
) -> RunRecord:
    return RunRecord(
        run_id=f"{agent}-{task_id}",
        task_id=task_id,
        agent=agent,
        config_hash=config,
        outcome=outcome,
        cost_usd=cost,
        latency_ms=latency_ms,
        turns=turns,
    )


def test_groups_by_agent_and_config() -> None:
    """A config change must not be averaged together with the config it replaced."""
    records = [
        _record("regin", "a", config="old"),
        _record("regin", "b", config="new"),
        _record("aider", "a", config="old"),
    ]
    scores = score(records)
    assert {(s.agent, s.config_hash) for s in scores} == {
        ("regin", "old"),
        ("regin", "new"),
        ("aider", "old"),
    }


def test_resolve_rate_uses_scorer_verdicts_when_supplied() -> None:
    """The agent's own claim of success is not evidence."""
    records = [
        _record("regin", "a", outcome=Outcome.RESOLVED),
        _record("regin", "b", outcome=Outcome.RESOLVED),
        _record("regin", "c", outcome=Outcome.RESOLVED),
        _record("regin", "d", outcome=Outcome.RESOLVED),
    ]
    graded = score(records, resolved_ids={"a"})
    assert graded[0].resolved == 1
    assert graded[0].resolve_rate == 0.25


def test_falls_back_to_terminal_outcome_without_verdicts() -> None:
    records = [
        _record("regin", "a", outcome=Outcome.RESOLVED),
        _record("regin", "b", outcome=Outcome.TURN_LIMIT),
    ]
    assert score(records)[0].resolve_rate == 0.5


def test_reports_median_cost_not_mean() -> None:
    """One runaway run must not move the headline cost figure."""
    records = [
        _record("regin", "a", cost=0.10),
        _record("regin", "b", cost=0.10),
        _record("regin", "c", cost=0.12),
        _record("regin", "d", cost=9.00),
    ]
    entry = score(records)[0]
    assert entry.median_cost_usd == 0.11
    assert entry.total_cost_usd == 9.32


def test_p95_latency_tracks_the_tail() -> None:
    """With 20 samples p95 is the 19th, so the slowest two runs set it."""
    records = [_record("regin", str(i), latency_ms=1000) for i in range(18)]
    records += [_record("regin", "slow1", latency_ms=60_000)]
    records += [_record("regin", "slow2", latency_ms=90_000)]
    assert score(records)[0].p95_latency_s == 60.0


def test_p95_is_not_moved_by_a_single_outlier() -> None:
    """Nearest-rank at N=20 puts p95 at the 19th value; one slow run must not set it."""
    records = [_record("regin", str(i), latency_ms=1000) for i in range(19)]
    records.append(_record("regin", "slow", latency_ms=60_000))
    assert score(records)[0].p95_latency_s == 1.0


def test_flags_an_agent_that_reports_no_cost() -> None:
    """Aider does not emit structured cost; the column must say so rather than show $0."""
    records = [_record("aider", "a", cost=0.0), _record("aider", "b", cost=0.0)]
    entry = score(records)[0]
    assert entry.cost_reported is False
    assert "n/a" in to_markdown([entry])


def test_markdown_table_renders_the_headline_number() -> None:
    markdown = to_markdown(score([_record("regin", "a")]))
    assert "resolve-rate" in markdown
    assert "100.0%" in markdown


def test_loader_skips_a_truncated_final_line(tmp_path: Path) -> None:
    """An interrupted sweep leaves a partial line; the rest must still be readable."""
    path = tmp_path / "records.jsonl"
    path.write_text(
        _record("regin", "a").to_jsonl() + '{"run_id": "partial", "task_',
        encoding="utf-8",
    )
    records = load_records(path)
    assert len(records) == 1
    assert records[0].task_id == "a"
