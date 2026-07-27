"""The scorer decides the published number, so its failure modes matter most.

The command runner is injected, so these tests exercise the real grading logic without
Docker or a repository checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scorers"))

from swebench import score_instance  # noqa: E402

TASK: dict[str, Any] = {
    "instance_id": "a__b-1",
    "base_commit": "abc123",
    "test_patch": "diff --git a/tests b/tests",
    "FAIL_TO_PASS": ["tests/test_x.py::test_target"],
    "PASS_TO_PASS": ["tests/test_x.py::test_existing"],
}


class FakeRunner:
    """Scripts git and pytest so grading logic can be tested in isolation."""

    def __init__(self, apply_ok: bool = True, results: dict[str, str] | None = None) -> None:
        self.apply_ok = apply_ok
        self.results = results or {}
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
        self.commands.append(command)
        if command[:2] == ["git", "apply"]:
            return (0, "") if self.apply_ok else (1, "patch does not apply")
        if "pytest" in command:
            lines = [f"{status} {test}" for test, status in self.results.items()]
            return 1, "\n".join(lines)
        return 0, ""


def _record(patch: str = "diff --git a/x b/x\n+fix\n") -> dict[str, Any]:
    return {"task_id": "a__b-1", "patch": patch}


def test_resolves_when_targets_pass_and_nothing_regresses(tmp_path: Path) -> None:
    runner = FakeRunner(
        results={
            "tests/test_x.py::test_target": "PASSED",
            "tests/test_x.py::test_existing": "PASSED",
        }
    )
    verdict = score_instance(_record(), TASK, tmp_path, runner)
    assert verdict.resolved is True
    assert verdict.fail_to_pass_passed == 1


def test_does_not_resolve_when_the_target_still_fails(tmp_path: Path) -> None:
    runner = FakeRunner(
        results={
            "tests/test_x.py::test_target": "FAILED",
            "tests/test_x.py::test_existing": "PASSED",
        }
    )
    verdict = score_instance(_record(), TASK, tmp_path, runner)
    assert verdict.resolved is False
    assert "0/1 target tests passed" in verdict.reason


def test_a_regression_defeats_a_passing_target(tmp_path: Path) -> None:
    """This is what stops 'delete the failing test' from scoring as a fix."""
    runner = FakeRunner(
        results={
            "tests/test_x.py::test_target": "PASSED",
            "tests/test_x.py::test_existing": "FAILED",
        }
    )
    verdict = score_instance(_record(), TASK, tmp_path, runner)
    assert verdict.resolved is False
    assert verdict.pass_to_pass_regressed == ["tests/test_x.py::test_existing"]


def test_an_empty_patch_never_resolves(tmp_path: Path) -> None:
    verdict = score_instance(_record(patch="   \n"), TASK, tmp_path, FakeRunner())
    assert verdict.resolved is False
    assert verdict.patch_applied is False
    assert verdict.reason == "empty patch"


def test_a_patch_that_does_not_apply_never_resolves(tmp_path: Path) -> None:
    verdict = score_instance(_record(), TASK, tmp_path, FakeRunner(apply_ok=False))
    assert verdict.resolved is False
    assert verdict.reason == "patch did not apply"


def test_resets_to_base_commit_before_applying(tmp_path: Path) -> None:
    """Without the reset, a previous instance's changes leak into this verdict."""
    runner = FakeRunner(results={"tests/test_x.py::test_target": "PASSED"})
    score_instance(_record(), TASK, tmp_path, runner)
    assert ["git", "checkout", "--force", "abc123"] in runner.commands
    assert ["git", "clean", "-fdx"] in runner.commands


def test_an_instance_with_no_target_tests_cannot_resolve(tmp_path: Path) -> None:
    """A malformed instance must not score as a free win."""
    task = {**TASK, "FAIL_TO_PASS": []}
    verdict = score_instance(_record(), task, tmp_path, FakeRunner())
    assert verdict.resolved is False
    assert "no FAIL_TO_PASS" in verdict.reason


def test_a_skipped_target_does_not_count_as_passing(tmp_path: Path) -> None:
    runner = FakeRunner(results={"tests/test_x.py::test_target": "SKIPPED"})
    verdict = score_instance(_record(), TASK, tmp_path, runner)
    assert verdict.resolved is False


def test_accepts_json_encoded_test_lists(tmp_path: Path) -> None:
    task = {**TASK, "FAIL_TO_PASS": '["tests/test_x.py::test_target"]', "PASS_TO_PASS": "[]"}
    runner = FakeRunner(results={"tests/test_x.py::test_target": "PASSED"})
    assert score_instance(_record(), task, tmp_path, runner).resolved is True


def test_cleans_up_the_temporary_patch_file(tmp_path: Path) -> None:
    score_instance(_record(), TASK, tmp_path, FakeRunner(apply_ok=False))
    assert not (tmp_path / ".regin-patch.diff").exists()


@pytest.mark.parametrize("status", ["FAILED", "ERROR"])
def test_both_failure_statuses_count_as_regressions(tmp_path: Path, status: str) -> None:
    runner = FakeRunner(
        results={
            "tests/test_x.py::test_target": "PASSED",
            "tests/test_x.py::test_existing": status,
        }
    )
    assert score_instance(_record(), TASK, tmp_path, runner).resolved is False
