from pathlib import Path

from regin.schemas import Outcome, RunRecord
from regin.trace import TraceStore


def _store(tmp_path: Path) -> TraceStore:
    return TraceStore(tmp_path / "runs.db", tmp_path / "records.jsonl")


def test_writes_a_record_to_both_sinks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(RunRecord(run_id="r1", task_id="t1", outcome=Outcome.RESOLVED, cost_usd=0.12))

    assert (tmp_path / "records.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert store.last()[0].run_id == "r1"


def test_jsonl_appends_one_line_per_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(RunRecord(run_id="r1", task_id="t1"))
    store.write(RunRecord(run_id="r2", task_id="t2"))

    assert (tmp_path / "records.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert len(store.last(limit=5)) == 2
