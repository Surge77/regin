from pathlib import Path

import pytest

from regin.sandbox.paths import PathEscapeError, safe_path


def test_resolves_a_relative_path_under_the_root(tmp_path: Path) -> None:
    assert safe_path(tmp_path, "src/main.py") == (tmp_path / "src" / "main.py").resolve()


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        safe_path(tmp_path, "../outside.txt")


def test_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        safe_path(tmp_path, "/etc/passwd")


def test_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        safe_path(tmp_path, "   ")
