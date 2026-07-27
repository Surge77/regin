"""regin CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from regin import __version__
from regin.agent import MissingAPIKeyError, build_agent
from regin.config import Config, Secrets
from regin.schemas import Outcome
from regin.trace import TraceStore

app = typer.Typer(add_completion=False, help="A terminal coding agent with a benchmark loop.")
trace_app = typer.Typer(help="Inspect run traces.")
app.add_typer(trace_app, name="trace")

console = Console()


@app.command()
def version() -> None:
    """Print the regin version."""
    console.print(f"regin {__version__}")


TASK_ARG = typer.Argument(..., help="What to fix, in plain language")
YES_OPT = typer.Option(False, "--yes", help="Auto-approve tools (benchmark mode)")
CWD_OPT = typer.Option(None, help="Repository root the agent may touch (default: cwd)")


@app.command()
def fix(task: str = TASK_ARG, yes: bool = YES_OPT, cwd: Path | None = CWD_OPT) -> None:
    """Run the agent against the current repository."""
    root = (cwd or Path.cwd()).resolve()
    config = Config.load()
    secrets = Secrets()

    try:
        loop, renderer = build_agent(root, config, secrets.anthropic_api_key, auto_approve=yes)
    except (MissingAPIKeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    record = asyncio.run(loop.run(task))

    store = TraceStore(config.trace.db_path, config.trace.jsonl_path)
    store.write(record)
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


if __name__ == "__main__":
    app()
