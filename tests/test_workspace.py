"""Patch extraction against a real local git repository.

No network: these use a repository created on the fly. `extract_patch` feeds the scorer
directly, so a bug here changes the published number.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from regin.bench.workspace import WorkspaceError, extract_patch


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test"], root)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)
    return root


async def test_an_untouched_repository_yields_an_empty_patch(repo: Path) -> None:
    assert (await extract_patch(repo)).strip() == ""


async def test_captures_a_modification(repo: Path) -> None:
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    patch = await extract_patch(repo)

    assert "calc.py" in patch
    assert "+    return a + b" in patch
    assert "-    return a - b" in patch


async def test_captures_a_newly_created_file(repo: Path) -> None:
    """A fix delivered as a new file would otherwise produce an empty patch and score as
    unresolved for entirely the wrong reason."""
    (repo / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    patch = await extract_patch(repo)

    assert "helper.py" in patch
    assert "+VALUE = 1" in patch


async def test_captures_a_deletion(repo: Path) -> None:
    (repo / "calc.py").unlink()
    patch = await extract_patch(repo)

    assert "calc.py" in patch
    assert "deleted file" in patch or "-def add" in patch


async def test_a_failing_git_invocation_raises_workspace_error(tmp_path: Path) -> None:
    """A checkout failure must be distinguishable from an agent failure."""
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()

    with pytest.raises(WorkspaceError):
        await extract_patch(not_a_repo)
