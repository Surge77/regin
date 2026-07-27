"""Session persistence and resume.

Phase 4.3. A run that hits the turn limit or the budget ceiling three quarters of the way
through a hard task has produced real work — files read, approaches ruled out, edits made.
Discarding all of it and starting over pays for that discovery twice.

Sessions are stored beside the trace database as one JSON file per run. The conversation is
persisted, not just the record, because resuming from a summary loses exactly the specifics
(line numbers, exact error text) that make the next turn cheap.

`config_hash` is stored and checked on resume: continuing a conversation that was produced
under a different prompt or model silently blends two configurations, and any number derived
from the result would belong to neither.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from regin.schemas import Message, Outcome, RunRecord


class ConfigMismatchError(RuntimeError):
    """The stored session came from a different configuration than the one resuming it."""


class Session(BaseModel):
    """One resumable conversation."""

    run_id: str
    task: str
    task_id: str = "adhoc"
    config_hash: str = ""
    model: str = ""
    outcome: Outcome = Outcome.ERROR
    turns: int = 0
    cost_usd: float = 0.0
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: list[Message] = Field(default_factory=list)

    @classmethod
    def from_run(cls, record: RunRecord, task: str, messages: list[Message]) -> Session:
        return cls(
            run_id=record.run_id,
            task=task,
            task_id=record.task_id,
            config_hash=record.config_hash,
            model=record.model,
            outcome=record.outcome,
            turns=record.turns,
            cost_usd=record.cost_usd,
            messages=messages,
        )

    @property
    def is_resumable(self) -> bool:
        """Only a run stopped by a limit is worth resuming.

        A resolved run has nothing left to do, and a run that died on a provider error has
        no useful state — the failure is in the environment, not the conversation.
        """
        return self.outcome in (Outcome.TURN_LIMIT, Outcome.BUDGET_EXCEEDED)


class SessionStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        return self._root / f"{run_id}.json"

    def save(self, session: Session) -> Path:
        path = self.path_for(session.run_id)
        path.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, run_id: str) -> Session:
        path = self.path_for(run_id)
        if not path.exists():
            raise FileNotFoundError(f"no session {run_id!r} under {self._root}")
        return Session.model_validate_json(path.read_text(encoding="utf-8"))

    def latest(self) -> Session | None:
        sessions = self.list()
        return sessions[0] if sessions else None

    def list(self, limit: int = 20) -> list[Session]:
        """Newest first. Unreadable files are skipped rather than fatal — one corrupt
        session must not make the rest unreachable."""
        sessions: list[Session] = []
        for path in self._root.glob("*.json"):
            try:
                sessions.append(Session.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        sessions.sort(key=lambda s: s.saved_at, reverse=True)
        return sessions[:limit]

    def resume_messages(self, session: Session, config_hash: str) -> list[Message]:
        """Return the stored conversation, refusing a configuration change.

        The `RESUMED` marker matters: without it the model sees a conversation that appears
        to have simply paused, and tends to re-run the commands it already ran.
        """
        if session.config_hash and session.config_hash != config_hash:
            raise ConfigMismatchError(
                f"session {session.run_id} ran under config {session.config_hash[:8]}, "
                f"but the current config is {config_hash[:8]}. Resuming would blend two "
                "configurations and make the result attributable to neither."
            )

        return [
            *session.messages,
            Message(
                role="user",
                content=(
                    "[RESUMED] The previous run stopped at a limit, not because the task was "
                    "finished. Continue from where it left off. Do not repeat work already "
                    "done above — re-read a file only if you need detail you no longer have."
                ),
            ),
        ]
