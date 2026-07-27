"""Execution sandboxes: a cwd jail for interactive use, Docker for benchmark runs."""

from regin.sandbox.paths import PathEscapeError, safe_path

__all__ = ["PathEscapeError", "safe_path"]
