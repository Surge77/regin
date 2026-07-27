"""The scorer against a real repository: real git, real patches, real pytest.

The unit tests script the command runner, which proves the grading *logic* but not that the
commands actually work. This file uses the real subprocess runner on a repository built on
the fly — no network, no Docker, no API key.

It is worth the slower runtime because this is the code that decides the published number.
A scorer that grades correctly against a fake and fails against git would produce a
confidently wrong benchmark.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scorers"))

from swebench import score_instance  # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"

TESTS = """\
from calc import add


def test_target():
    assert add(2, 3) == 5


def test_existing():
    assert add(0, 0) == 0
"""


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, Any]:
    """A repository at a base commit, plus the task metadata the scorer expects."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "T"], root)
    _git(["config", "commit.gpgsign", "false"], root)

    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    # conftest puts the repo root on sys.path so `from calc import add` resolves.
    (root / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "base"], root)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    return {
        "root": root,
        "task": {
            "instance_id": "local__calc-1",
            "base_commit": head,
            "test_patch": "",  # the graded tests are already committed here
            "FAIL_TO_PASS": ["tests/test_calc.py::test_target"],
            "PASS_TO_PASS": ["tests/test_calc.py::test_existing"],
        },
    }


def _patch(root: Path, new_source: str) -> str:
    """Produce a real unified diff the way the runner does, then reset the tree."""
    (root / "calc.py").write_text(new_source, encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff", "--no-color"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    _git(["checkout", "--", "."], root)
    return diff


# -B: the fix is byte-identical in length to the bug, so a .pyc written in the same second
# would be reused and the graded run would see stale bytecode. See test_loop_e2e.
@pytest.fixture(autouse=True)
def no_bytecode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


def test_a_correct_patch_resolves(repo: dict[str, Any]) -> None:
    root: Path = repo["root"]
    record = {"task_id": "local__calc-1", "patch": _patch(root, FIXED)}

    verdict = score_instance(record, repo["task"], root)

    assert verdict.patch_applied is True
    assert verdict.resolved is True
    assert verdict.fail_to_pass_passed == 1
    assert verdict.pass_to_pass_regressed == []


def test_a_patch_that_does_not_fix_the_bug_does_not_resolve(repo: dict[str, Any]) -> None:
    root: Path = repo["root"]
    record = {"task_id": "local__calc-1", "patch": _patch(root, "def add(a, b):\n    return 0\n")}

    verdict = score_instance(record, repo["task"], root)

    assert verdict.patch_applied is True
    assert verdict.resolved is False


def test_deleting_the_failing_test_does_not_resolve(repo: dict[str, Any]) -> None:
    """The cheapest way to fake a fix. Rule 3 exists to catch exactly this."""
    root: Path = repo["root"]
    (root / "tests" / "test_calc.py").write_text(
        "def test_target():\n    pass\n", encoding="utf-8"
    )
    diff = subprocess.run(
        ["git", "diff", "--no-color"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    _git(["checkout", "--", "."], root)

    verdict = score_instance({"task_id": "x", "patch": diff}, repo["task"], root)

    assert verdict.resolved is False


def test_a_fix_that_breaks_an_existing_test_does_not_resolve(repo: dict[str, Any]) -> None:
    """test_target passes, test_existing regresses — a real fix must do neither."""
    root: Path = repo["root"]
    sneaky = "def add(a, b):\n    if a == 0 and b == 0:\n        return 99\n    return a + b\n"
    record = {"task_id": "local__calc-1", "patch": _patch(root, sneaky)}

    verdict = score_instance(record, repo["task"], root)

    assert verdict.fail_to_pass_passed == 1
    assert verdict.pass_to_pass_regressed == ["tests/test_calc.py::test_existing"]
    assert verdict.resolved is False


def test_a_malformed_patch_is_reported_not_crashed(repo: dict[str, Any]) -> None:
    root: Path = repo["root"]
    record = {"task_id": "x", "patch": "this is not a diff at all\n"}

    verdict = score_instance(record, repo["task"], root)

    assert verdict.resolved is False
    assert verdict.patch_applied is False
    assert verdict.reason == "patch did not apply"


def test_grading_is_idempotent_on_a_dirty_tree(repo: dict[str, Any]) -> None:
    """The scorer resets to base_commit at the *start* of grading, not the end.

    That ordering is what matters: a checkout reused across instances always starts clean,
    so one instance's patch cannot leak into the next one's verdict. Grading the same
    record twice must therefore give the same answer rather than failing to apply onto a
    tree that already carries the patch.
    """
    root: Path = repo["root"]
    good = _patch(root, FIXED)

    first = score_instance({"task_id": "x", "patch": good}, repo["task"], root)
    second = score_instance({"task_id": "x", "patch": good}, repo["task"], root)

    assert first.resolved is True
    assert second.resolved is True

    # A different patch grades correctly straight after, without a manual clean in between.
    bad = _patch(root, "def add(a, b):\n    return 0\n")
    assert score_instance({"task_id": "x", "patch": bad}, repo["task"], root).resolved is False


def test_a_prior_instances_changes_cannot_leak_into_a_verdict(repo: dict[str, Any]) -> None:
    """Simulate a reused checkout left dirty by whatever ran before."""
    root: Path = repo["root"]
    good = _patch(root, FIXED)

    # Leave unrelated debris behind, as an interrupted run would.
    (root / "calc.py").write_text("def add(a, b):\n    raise RuntimeError('debris')\n", "utf-8")
    (root / "stray.txt").write_text("junk", encoding="utf-8")

    verdict = score_instance({"task_id": "x", "patch": good}, repo["task"], root)

    assert verdict.resolved is True
    assert not (root / "stray.txt").exists()
