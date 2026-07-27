"""Terminal rendering. Presentation only — no control flow lives here.

Kept deliberately plain: this is a benchmark harness that happens to have a TUI, and
anything that cannot run headless under `--yes` in a container is a liability.
"""

from __future__ import annotations

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from regin.schemas import RunRecord, ToolCall, ToolResult

# Long tool output is already truncated for the model; this is the human-facing cap.
PREVIEW_LINES = 12


class Renderer:
    def __init__(self, console: Console | None = None, quiet: bool = False) -> None:
        self._console = console or Console()
        self._quiet = quiet
        self._streaming = False

    def text(self, chunk: str) -> None:
        """Stream assistant text as it arrives."""
        if self._quiet:
            return
        self._streaming = True
        self._console.print(chunk, end="", highlight=False, markup=False)

    def end_text(self) -> None:
        if self._streaming and not self._quiet:
            self._console.print()
        self._streaming = False

    def turn(self, number: int, cost_usd: float) -> None:
        if self._quiet:
            return
        self._console.rule(f"[dim]turn {number} · ${cost_usd:.4f}[/]", style="dim")

    def tool_call(self, call: ToolCall) -> None:
        if self._quiet:
            return
        self.end_text()
        detail = ", ".join(f"{k}={_short(v)}" for k, v in call.arguments.items())
        self._console.print(
            Text.assemble(("→ ", "cyan"), (call.name, "bold cyan"), (f"({detail})", "dim"))
        )

    def tool_result(self, result: ToolResult) -> None:
        if self._quiet:
            return
        style = "red" if result.is_error else "green"
        lines = result.content.splitlines()
        for line in lines[:PREVIEW_LINES]:
            self._console.print(Text(f"  {line}", style=style if result.is_error else "dim"))
        if len(lines) > PREVIEW_LINES:
            self._console.print(Text(f"  … {len(lines) - PREVIEW_LINES} more lines", style="dim"))

    def diff(self, path: str, content: str) -> None:
        if self._quiet:
            return
        self._console.print(Syntax(content, _lexer(path), theme="ansi_dark", line_numbers=False))

    def summary(self, record: RunRecord) -> None:
        """The four numbers the benchmark actually reports, per run."""
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_row("outcome", record.outcome.value)
        table.add_row("turns", str(record.turns))
        table.add_row("cost", f"${record.cost_usd:.4f}")
        table.add_row("latency", f"{record.latency_ms / 1000:.1f}s")
        usage = record.usage
        table.add_row(
            "tokens",
            f"in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={usage.cache_read_tokens} cache_write={usage.cache_write_tokens}",
        )
        if record.error:
            table.add_row("error", record.error)
        self._console.print(table)


def _short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


def _lexer(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1] if "." in path else ""
    return {"py": "python", "toml": "toml", "json": "json", "md": "markdown"}.get(suffix, "text")
