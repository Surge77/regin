"""`regin bench` — run a sweep, then score it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from regin.bench.agents import AiderAgent, ClaudeCodeAgent, ReginAgent
from regin.bench.runner import BenchAgent, SweepConfig, run_sweep
from regin.bench.scorecard import load_records, render, score, to_markdown
from regin.bench.task import load_tasks
from regin.config import Config, Secrets
from regin.trace import TraceStore

bench_app = typer.Typer(help="Run and score the benchmark sweep.")
console = Console()

AGENTS = {"regin": ReginAgent, "claude-code": ClaudeCodeAgent, "aider": AiderAgent}

TASKS_OPT = typer.Option(..., "--tasks", help="JSONL file of SWE-bench instances")
AGENT_OPT = typer.Option("regin", "--agent", help=f"One of: {', '.join(AGENTS)}")
OUT_OPT = typer.Option(Path("runs/bench"), "--out", help="Output directory")
LIMIT_OPT = typer.Option(None, "--limit", help="Only run the first N instances")
CONC_OPT = typer.Option(4, "--concurrency", help="Instances in flight at once")
BUDGET_OPT = typer.Option(25.0, "--max-cost", help="Hard USD ceiling for the whole sweep")
NO_DOCKER_OPT = typer.Option(
    False, "--no-docker", help="Run tests on the host instead of in a container (unsafe)"
)


@bench_app.command("run")
def bench_run(
    tasks: Path = TASKS_OPT,
    agent: str = AGENT_OPT,
    out: Path = OUT_OPT,
    limit: int | None = LIMIT_OPT,
    concurrency: int = CONC_OPT,
    max_cost: float = BUDGET_OPT,
    no_docker: bool = NO_DOCKER_OPT,
) -> None:
    """Run one agent across a task set, writing a JSONL record per instance."""
    if agent not in AGENTS:
        console.print(f"[red]unknown agent {agent!r}; choose from {', '.join(AGENTS)}[/]")
        raise typer.Exit(2)

    config = Config.load()
    secrets = Secrets()
    instances = load_tasks(tasks, limit=limit)
    if not instances:
        console.print(f"[red]no instances loaded from {tasks}[/]")
        raise typer.Exit(2)

    out.mkdir(parents=True, exist_ok=True)
    sweep_config = SweepConfig(
        tasks_path=tasks,
        output_dir=out,
        concurrency=concurrency,
        max_sweep_cost_usd=max_cost,
        limit=limit,
    )

    runner: BenchAgent = (
        ReginAgent(config, secrets.anthropic_api_key, use_docker=not no_docker)
        if agent == "regin"
        else AGENTS[agent](config)  # pyright: ignore[reportCallIssue]
    )

    store = TraceStore(out / "bench.db", out / f"{agent}-records.jsonl")
    console.print(
        f"[bold]{agent}[/] · {len(instances)} instances · "
        f"concurrency {concurrency} · ceiling ${max_cost:.2f}"
    )

    result = asyncio.run(run_sweep(instances, runner, sweep_config, store))

    if result.stopped_early:
        console.print("[yellow]sweep stopped early: cost ceiling reached[/]")
    if result.skipped:
        console.print(f"[yellow]{len(result.skipped)} instance(s) skipped[/]")

    console.print(
        f"attempted {len(result.records)} · "
        f"clean finish {result.resolve_rate:.1%} · "
        f"${sum(r.cost_usd for r in result.records):.2f} · "
        f"{result.wall_clock_s / 60:.1f} min"
    )
    console.print(
        "[dim]This is a completion rate, not a resolve-rate. "
        "Run `regin bench score` for the graded number.[/]"
    )


RECORDS_OPT = typer.Option(
    None,
    "--records",
    help="JSONL run records. Repeat the flag, or pass a glob — both work in any shell.",
)
RECORDS_ARG = typer.Argument(
    None,
    help="Record files, for when your shell already expanded the glob.",
)
VERDICTS_OPT = typer.Option(
    None, "--verdicts", help="evalgate verdicts JSON; without it only completion is reported"
)
MARKDOWN_OPT = typer.Option(None, "--markdown", help="Write the scorecard table to a file")


@bench_app.command("score")
def bench_score(
    files: list[Path] | None = RECORDS_ARG,
    records: list[Path] | None = RECORDS_OPT,
    verdicts: Path | None = VERDICTS_OPT,
    markdown: Path | None = MARKDOWN_OPT,
) -> None:
    """Aggregate run records into the comparison table."""
    # Both spellings feed one list: whether a quoted glob survives to the program or is
    # expanded by the shell is the shell's decision, not something the user should have to
    # think about when copying a command out of the README.
    requested = [*(records or []), *(files or [])]
    if not requested:
        console.print("[red]pass at least one record file, via --records or positionally[/]")
        raise typer.Exit(2)

    paths = _expand(requested)
    all_records = [record for path in paths for record in load_records(path)]

    if not all_records:
        shown = ", ".join(str(entry) for entry in requested)
        console.print(f"[red]no records found at {shown}[/]")
        raise typer.Exit(2)

    resolved_ids: set[str] | None = None
    if verdicts is not None:
        payload = json.loads(verdicts.read_text(encoding="utf-8"))
        resolved_ids = {
            entry["instance_id"] for entry in payload.get("instances", []) if entry.get("resolved")
        }

    scores = score(all_records, resolved_ids)
    console.print(render(scores))

    if resolved_ids is None:
        console.print(
            "[yellow]No verdicts supplied — the resolve column is the agent's own "
            "completion signal, not a graded result. Do not publish it as a resolve-rate.[/]"
        )

    if markdown is not None:
        markdown.write_text(to_markdown(scores) + "\n", encoding="utf-8")
        console.print(f"[green]wrote {markdown}[/]")


def _expand(entries: list[Path]) -> list[Path]:
    """Resolve each entry, which may be a literal path or an unexpanded glob.

    Both forms have to work. Shells differ on whether a quoted glob reaches the program
    unexpanded, so a command copied from the README should not fail on the user's shell —
    and repeating `--records` is the portable escape hatch when it does.
    """
    paths: list[Path] = []
    for entry in entries:
        if any(char in entry.name for char in "*?["):
            paths.extend(sorted(entry.parent.glob(entry.name)))
        else:
            paths.append(entry)
    # Deduplicated: a glob and an explicit path can name the same file, and counting a
    # record twice would silently distort every figure in the table.
    return list(dict.fromkeys(paths))
