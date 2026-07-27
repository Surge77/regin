"""Search tools: gitignore-aware globbing and ripgrep-backed content search.

Both are capped hard. An unbounded `glob **/*` on a large repo can return tens of
thousands of paths, which costs more context than the answer is worth.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pathspec
from pydantic import BaseModel, Field

from regin.sandbox.paths import safe_path

MAX_GLOB_RESULTS = 200
MAX_GREP_MATCHES = 100
SEARCH_TIMEOUT_S = 30

# Always pruned, whether or not a .gitignore mentions them.
ALWAYS_IGNORED = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}


class GlobArgs(BaseModel):
    pattern: str = Field(description="Glob pattern, e.g. 'src/**/*.py'")
    path: str = Field(default=".", description="Directory to search from, relative to the root")


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regular expression to search for")
    path: str = Field(default=".", description="File or directory to search, relative to the root")
    glob: str = Field(default="", description="Optional file filter, e.g. '*.py'")


class RipgrepMissingError(RuntimeError):
    """ripgrep is not installed. Recoverable: the model can fall back to glob + read_file."""


def load_ignore_spec(root: Path) -> pathspec.PathSpec:
    """Build a matcher from the repo's .gitignore. Absent file means nothing is ignored."""
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _is_ignored(spec: pathspec.PathSpec, root: Path, target: Path) -> bool:
    if ALWAYS_IGNORED & set(target.relative_to(root).parts):
        return True
    return spec.match_file(target.relative_to(root).as_posix())


class GlobTool:
    name = "glob"
    description = (
        "Find files by glob pattern. Respects .gitignore. "
        f"Returns at most {MAX_GLOB_RESULTS} paths, sorted."
    )
    args_model = GlobArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, GlobArgs)
        base = safe_path(self._root, args.path, "glob path")
        spec = load_ignore_spec(self._root)

        matches = sorted(
            path.relative_to(self._root).as_posix()
            for path in base.glob(args.pattern)
            if path.is_file() and not _is_ignored(spec, self._root, path)
        )

        if not matches:
            return f"no files match {args.pattern!r} under {args.path}"

        shown = matches[:MAX_GLOB_RESULTS]
        header = f"{len(matches)} match(es)"
        if len(matches) > MAX_GLOB_RESULTS:
            header += f", showing first {MAX_GLOB_RESULTS} — narrow the pattern"
        return header + ":\n" + "\n".join(shown)


class GrepTool:
    name = "grep"
    description = (
        "Search file contents with a regular expression, via ripgrep. "
        "Respects .gitignore. Returns 'path:line: text' per match."
    )
    args_model = GrepArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, GrepArgs)
        target = safe_path(self._root, args.path, "grep path")

        command = ["rg", "--json", "--max-count", str(MAX_GREP_MATCHES), args.pattern]
        if args.glob:
            command += ["--glob", args.glob]
        command.append(str(target))

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # pragma: no cover - environment dependent
            raise RipgrepMissingError(
                "ripgrep (rg) is not installed; use glob + read_file instead"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), SEARCH_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"grep timed out after {SEARCH_TIMEOUT_S}s — narrow the pattern or path"

        # rg exits 1 for "no matches", which is a normal result, not a failure.
        if proc.returncode not in (0, 1):
            return f"grep failed: {stderr.decode(errors='replace').strip()}"

        matches = _parse_ripgrep_json(stdout.decode(errors="replace"), self._root)
        if not matches:
            return f"no matches for {args.pattern!r}"

        header = f"{len(matches)} match(es)"
        if len(matches) >= MAX_GREP_MATCHES:
            header += " (capped — narrow the pattern)"
        return header + ":\n" + "\n".join(matches[:MAX_GREP_MATCHES])


def _parse_ripgrep_json(stdout: str, root: Path) -> list[str]:
    """Flatten rg's JSON event stream to 'path:line: text' lines.

    rg emits one JSON object per line with mixed event types; only 'match' carries a hit.
    Malformed lines are skipped rather than failing the whole search.
    """
    results: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue

        data = event.get("data", {})
        raw_path = data.get("path", {}).get("text", "")
        try:
            display = Path(raw_path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            display = raw_path
        text = data.get("lines", {}).get("text", "").rstrip("\n")
        results.append(f"{display}:{data.get('line_number', 0)}: {text}")
    return results
