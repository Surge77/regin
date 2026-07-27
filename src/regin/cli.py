"""regin CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from regin import __version__
from regin.agent import MissingAPIKeyError, build_agent
from regin.bench.cli import bench_app
from regin.config import Config, Secrets
from regin.schemas import Outcome, config_hash
from regin.session import ConfigMismatchError, Session, SessionStore
from regin.trace import TraceStore

app = typer.Typer(add_completion=False, help="A terminal coding agent with a benchmark loop.")
trace_app = typer.Typer(help="Inspect run traces.")
session_app = typer.Typer(help="Inspect and resume saved sessions.")
app.add_typer(trace_app, name="trace")
app.add_typer(bench_app, name="bench")
app.add_typer(session_app, name="session")

console = Console()


@app.command()
def version() -> None:
    """Print the regin version."""
    console.print(f"regin {__version__}")


TASK_ARG = typer.Argument(..., help="What to fix, in plain language")
YES_OPT = typer.Option(False, "--yes", help="Auto-approve tools (benchmark mode)")
CWD_OPT = typer.Option(None, help="Repository root the agent may touch (default: cwd)")


RESUME_OPT = typer.Option(
    None, "--resume", help="Continue a previous run by run_id, or 'last' for the most recent"
)


@app.command()
def fix(
    task: str = TASK_ARG,
    yes: bool = YES_OPT,
    cwd: Path | None = CWD_OPT,
    resume: str | None = RESUME_OPT,
) -> None:
    """Run the agent against the current repository."""
    root = (cwd or Path.cwd()).resolve()
    config = Config.load()
    secrets = Secrets()

    try:
        loop, renderer = build_agent(root, config, secrets.anthropic_api_key, auto_approve=yes)
    except (MissingAPIKeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    sessions = SessionStore(config.trace.sessions_path)
    resume_from = None
    if resume is not None:
        try:
            previous = sessions.latest() if resume == "last" else sessions.load(resume)
            if previous is None:
                raise FileNotFoundError("no sessions recorded yet")
            resume_from = sessions.resume_messages(previous, config_hash(config.hashable()))
            task = previous.task
            console.print(f"[dim]resuming {previous.run_id} ({previous.turns} turns so far)[/]")
        except (FileNotFoundError, ConfigMismatchError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(2) from exc

    record = asyncio.run(loop.run(task, resume_from=resume_from))

    store = TraceStore(config.trace.db_path, config.trace.jsonl_path)
    store.write(record)

    session = Session.from_run(record, task, loop.messages)
    sessions.save(session)
    if session.is_resumable:
        console.print("[yellow]stopped at a limit — resume with:[/] regin fix '' --resume last")

    renderer.summary(record)

    # Non-zero exit on anything but a clean finish, so CI and the bench runner can branch
    # on the process result without parsing stdout.
    if record.outcome is not Outcome.RESOLVED:
        raise typer.Exit(1)


@trace_app.command("last")
def trace_last(limit: int = typer.Option(1, help="How many runs to show")) -> None:
    """Show the most recent runs."""
    config = Config.load()
    store = TraceStore(config.trace.db_path, config.trace.jsonl_path)
    records = store.last(limit)

    if not records:
        console.print("[dim]no runs recorded yet[/]")
        return

    table = Table("run", "task", "outcome", "turns", "cost", "latency")
    for record in records:
        table.add_row(
            record.run_id,
            record.task_id,
            record.outcome.value,
            str(record.turns),
            f"${record.cost_usd:.4f}",
            f"{record.latency_ms} ms",
        )
    console.print(table)



@session_app.command("list")
def session_list(limit: int = typer.Option(10, help="How many sessions to show")) -> None:
    """Show saved sessions, newest first."""
    config = Config.load()
    sessions = SessionStore(config.trace.sessions_path).list(limit)

    if not sessions:
        console.print("[dim]no sessions saved yet[/]")
        return

    table = Table("run", "task", "outcome", "turns", "cost", "resumable")
    for session in sessions:
        table.add_row(
            session.run_id,
            session.task[:48] + ("…" if len(session.task) > 48 else ""),
            session.outcome.value,
            str(session.turns),
            f"${session.cost_usd:.4f}",
            "yes" if session.is_resumable else "no",
        )
    console.print(table)

if __name__ == "__main__":
    app()
