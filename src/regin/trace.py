"""Run traces: SQLite for session history, JSONL for the evalgate contract."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from regin.schemas import RunRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    agent       TEXT NOT NULL,
    model       TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    turns       INTEGER NOT NULL,
    cost_usd    REAL NOT NULL,
    latency_ms  INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    record      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_config ON runs(config_hash);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
"""


class TraceStore:
    def __init__(self, db_path: Path, jsonl_path: Path) -> None:
        self._db_path = db_path
        self._jsonl_path = jsonl_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def write(self, record: RunRecord) -> None:
        """Persist to both sinks. JSONL is what evalgate reads."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.task_id,
                    record.agent,
                    record.model,
                    record.config_hash,
                    record.outcome.value,
                    record.turns,
                    record.cost_usd,
                    record.latency_ms,
                    record.started_at.isoformat(),
                    record.finished_at.isoformat() if record.finished_at else None,
                    record.model_dump_json(),
                ),
            )
        with self._jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(record.to_jsonl())

    def last(self, limit: int = 1) -> list[RunRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT record FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RunRecord.model_validate_json(row[0]) for row in rows]
