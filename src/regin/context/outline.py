"""Repository outline — structure instead of file dumps.

Phase 3.2. Reading three 800-line files to find one function costs thousands of tokens and
buries the answer. An outline gives the model the shape of the codebase for a fraction of
that, so it can then read only what matters.

Uses the standard library's `ast` rather than tree-sitter: Python is the language the
benchmark actually measures, `ast` is exact where a grammar-agnostic parse is approximate,
and it adds no dependency. Non-Python files fall back to a regex scan that is deliberately
shallow — a rough signature list still beats no signal, and pretending otherwise would be
worse than admitting the limit.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from regin.tools.search import ALWAYS_IGNORED, load_ignore_spec

MAX_FILES = 300
MAX_SYMBOLS_PER_FILE = 40

# Rough signature match for languages we do not parse properly.
FALLBACK_SIGNATURE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|def|fn|type|interface|struct)\s+(\w+)",
    re.MULTILINE,
)


@dataclass
class FileOutline:
    path: str
    symbols: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str = ""

    def render(self) -> str:
        if self.error:
            return f"{self.path}\n  ! {self.error}"
        if not self.symbols:
            return f"{self.path}"

        lines = [self.path]
        lines.extend(f"  {symbol}" for symbol in self.symbols[:MAX_SYMBOLS_PER_FILE])
        if self.truncated or len(self.symbols) > MAX_SYMBOLS_PER_FILE:
            hidden = max(0, len(self.symbols) - MAX_SYMBOLS_PER_FILE)
            lines.append(f"  … {hidden} more")
        return "\n".join(lines)


def outline_file(path: Path, display: str | None = None) -> FileOutline:
    name = display or path.name
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileOutline(name, error=f"unreadable: {type(exc).__name__}")

    if path.suffix == ".py":
        return _outline_python(source, name)
    return _outline_fallback(source, name)


def _outline_python(source: str, name: str) -> FileOutline:
    """Top-level definitions plus one level of class members.

    Deliberately shallow: nested helpers and closures are implementation detail, and
    including them would reintroduce the noise the outline exists to remove.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # A syntax error is worth surfacing — the agent may have just caused it.
        return FileOutline(name, error=f"syntax error at line {exc.lineno}")

    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
            symbols.extend(
                f"  {_signature(child)}"
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(_signature(node))

    return FileOutline(name, symbols)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Name and parameters only. Types and defaults are noise at this altitude."""
    args = [arg.arg for arg in node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def _outline_fallback(source: str, name: str) -> FileOutline:
    matches = FALLBACK_SIGNATURE.finditer(source)
    return FileOutline(name, [match.group(0).strip() for match in matches])


def outline_repo(root: Path, pattern: str = "**/*.py", max_files: int = MAX_FILES) -> str:
    """Render an outline of the repository, respecting .gitignore.

    Capped: past a few hundred files the outline itself becomes the context problem it was
    meant to solve, and the honest response is to say so rather than truncate silently.
    """
    spec = load_ignore_spec(root)
    paths = sorted(
        path
        for path in root.glob(pattern)
        if path.is_file()
        and not (ALWAYS_IGNORED & set(path.relative_to(root).parts))
        and not spec.match_file(path.relative_to(root).as_posix())
    )

    if not paths:
        return f"no files match {pattern!r}"

    shown = paths[:max_files]
    blocks = [outline_file(path, path.relative_to(root).as_posix()).render() for path in shown]

    header = f"{len(paths)} file(s)"
    if len(paths) > max_files:
        header += f", outlining first {max_files} — narrow the pattern"
    return f"{header}:\n\n" + "\n".join(blocks)
