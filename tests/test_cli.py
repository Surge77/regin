"""CLI wiring and the interactive permission prompter."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from regin.bench.scorecard import score
from regin.cli import app
from regin.permissions import ConsolePrompter
from regin.schemas import Outcome, RunRecord

runner = CliRunner()


@pytest.fixture
def in_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_version_prints_the_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "regin" in result.stdout


def test_fix_fails_clearly_without_an_api_key(
    in_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing credential must name itself, not surface as a provider stack trace."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    result = runner.invoke(app, ["fix", "do something"])

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.stdout


def test_trace_last_reports_an_empty_history(in_tmp_dir: Path) -> None:
    result = runner.invoke(app, ["trace", "last"])
    assert result.exit_code == 0
    assert "no runs recorded" in result.stdout


def test_bench_rejects_an_unknown_agent(in_tmp_dir: Path) -> None:
    tasks = in_tmp_dir / "tasks.jsonl"
    tasks.write_text("", encoding="utf-8")
    result = runner.invoke(app, ["bench", "run", "--tasks", str(tasks), "--agent", "nope"])

    assert result.exit_code == 2
    assert "unknown agent" in result.stdout


def test_bench_run_refuses_an_empty_task_set(in_tmp_dir: Path) -> None:
    tasks = in_tmp_dir / "tasks.jsonl"
    tasks.write_text("", encoding="utf-8")
    result = runner.invoke(app, ["bench", "run", "--tasks", str(tasks)])

    assert result.exit_code == 2
    assert "no instances" in result.stdout


def test_bench_score_renders_a_table(in_tmp_dir: Path) -> None:
    records = in_tmp_dir / "records.jsonl"
    records.write_text(
        RunRecord(
            run_id="r1", task_id="t1", agent="regin", outcome=Outcome.RESOLVED, cost_usd=0.1
        ).to_jsonl(),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["bench", "score", "--records", str(records)])

    assert result.exit_code == 0
    assert "regin" in result.stdout


def test_bench_score_warns_when_the_number_is_ungraded(in_tmp_dir: Path) -> None:
    """Publishing a completion rate as a resolve-rate is the easiest way to lie here."""
    records = in_tmp_dir / "records.jsonl"
    records.write_text(
        RunRecord(run_id="r1", task_id="t1", agent="regin", outcome=Outcome.RESOLVED).to_jsonl(),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["bench", "score", "--records", str(records)])

    assert "not a graded result" in result.stdout.replace("\n", " ")


def test_bench_score_applies_evalgate_verdicts(in_tmp_dir: Path) -> None:
    records = in_tmp_dir / "records.jsonl"
    records.write_text(
        RunRecord(run_id="r1", task_id="t1", agent="regin", outcome=Outcome.RESOLVED).to_jsonl()
        + RunRecord(run_id="r2", task_id="t2", agent="regin", outcome=Outcome.RESOLVED).to_jsonl(),
        encoding="utf-8",
    )
    verdicts = in_tmp_dir / "verdicts.json"
    verdicts.write_text(
        '{"instances": [{"instance_id": "t1", "resolved": true}, '
        '{"instance_id": "t2", "resolved": false}]}',
        encoding="utf-8",
    )
    markdown = in_tmp_dir / "table.md"

    result = runner.invoke(
        app,
        [
            "bench",
            "score",
            "--records",
            str(records),
            "--verdicts",
            str(verdicts),
            "--markdown",
            str(markdown),
        ],
    )

    assert result.exit_code == 0
    assert "50.0%" in markdown.read_text(encoding="utf-8")


def _records_file(path: Path, agent: str, count: int = 2) -> Path:
    path.write_text(
        "".join(
            RunRecord(
                run_id=f"{agent}-{i}", task_id=f"t{i}", agent=agent, outcome=Outcome.RESOLVED
            ).to_jsonl()
            for i in range(count)
        ),
        encoding="utf-8",
    )
    return path


def test_bench_score_accepts_positional_files(in_tmp_dir: Path) -> None:
    """Shells differ on whether a quoted glob survives to the program. When the shell
    expands it first, the paths arrive positionally and must still work."""
    a = _records_file(in_tmp_dir / "regin-records.jsonl", "regin")
    b = _records_file(in_tmp_dir / "aider-records.jsonl", "aider")

    result = runner.invoke(app, ["bench", "score", str(a), str(b)])

    assert result.exit_code == 0
    assert "regin" in result.stdout
    assert "aider" in result.stdout


def test_bench_score_accepts_a_repeated_flag(in_tmp_dir: Path) -> None:
    a = _records_file(in_tmp_dir / "regin-records.jsonl", "regin")
    b = _records_file(in_tmp_dir / "aider-records.jsonl", "aider")

    result = runner.invoke(app, ["bench", "score", "--records", str(a), "--records", str(b)])

    assert result.exit_code == 0
    assert "aider" in result.stdout


def test_bench_score_accepts_an_unexpanded_glob(in_tmp_dir: Path) -> None:
    _records_file(in_tmp_dir / "regin-records.jsonl", "regin")
    _records_file(in_tmp_dir / "aider-records.jsonl", "aider")

    result = runner.invoke(
        app, ["bench", "score", "--records", str(in_tmp_dir / "*-records.jsonl")]
    )

    assert result.exit_code == 0
    assert "regin" in result.stdout
    assert "aider" in result.stdout


def test_bench_score_counts_a_record_once_when_named_twice(in_tmp_dir: Path) -> None:
    """A glob and an explicit path can name the same file; double-counting would silently
    distort every figure in the table."""
    a = _records_file(in_tmp_dir / "regin-records.jsonl", "regin", count=3)

    result = runner.invoke(
        app,
        ["bench", "score", str(a), "--records", str(in_tmp_dir / "*-records.jsonl")],
    )

    assert result.exit_code == 0
    # n must be 3, not 6.
    assert " 3 " in result.stdout


def test_bench_score_requires_at_least_one_file(in_tmp_dir: Path) -> None:
    result = runner.invoke(app, ["bench", "score"])

    assert result.exit_code == 2
    assert "at least one record file" in result.stdout


def test_bench_score_errors_on_missing_records(in_tmp_dir: Path) -> None:
    empty = in_tmp_dir / "none.jsonl"
    empty.write_text("", encoding="utf-8")
    result = runner.invoke(app, ["bench", "score", "--records", str(empty)])

    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("", False), ("maybe", False)],
)
def test_console_prompter_only_accepts_an_explicit_yes(
    monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)  # pyright: ignore[reportUnknownLambdaType]
    assert ConsolePrompter().confirm("bash", "bash(rm -rf /)") is expected


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_console_prompter_treats_an_interrupt_as_refusal(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    """A closed stdin or a Ctrl-C must never be read as approval."""

    def _raise(_: str) -> str:
        raise error()

    monkeypatch.setattr("builtins.input", _raise)
    assert ConsolePrompter().confirm("bash", "bash(x)") is False


def test_scorecard_handles_an_empty_record_set() -> None:
    assert score([]) == []
