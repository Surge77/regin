"""Path jail. No model-supplied path reaches the filesystem without passing through here.

Ported from helix-cli's resolveSafePath — the one piece of that project worth keeping.
"""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """A path resolved outside its permitted root."""


def safe_path(root: Path, candidate: str, label: str = "path") -> Path:
    """Resolve `candidate` under `root`, refusing anything that escapes.

    Handles `..`, absolute paths, and symlinks — resolution happens before the
    containment check, so a symlink pointing outside the root is caught.
    """
    if not candidate or not candidate.strip():
        raise PathEscapeError(f"empty {label}")

    base = root.resolve()
    target = (base / candidate).resolve()

    if target != base and base not in target.parents:
        raise PathEscapeError(f"{label} escapes the working directory: {candidate}")

    return target
