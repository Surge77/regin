"""`swebench` scorer plugin for evalgate.

Lives in `regin` but is written against evalgate's plugin contract, and is installed into
evalgate rather than imported by it. The two repos share one thing — the JSONL run record
— and this file is the only place that knows both shapes.

The verdict is deliberately not the agent's opinion. A run record says how the loop
terminated; this decides whether the patch actually resolves the instance:

  1. the patch applies cleanly to the repository at `base_commit`,
  2. every FAIL_TO_PASS test passes afterwards,
  3. every PASS_TO_PASS test still passes.

Rule 3 is what stops "delete the failing test" from scoring as a fix.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APPLY_TIMEOUT_S = 120
TEST_TIMEOUT_S = 1800

# pytest's terse per-test result lines, e.g. "PASSED tests/test_x.py::test_y".
RESULT_LINE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(\S+)", re.MULTILINE)


@dataclass
class InstanceVerdict:
    instance_id: str
    resolved: bool
    patch_applied: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_regressed: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "resolved": self.resolved,
            "patch_applied": self.patch_applied,
            "fail_to_pass": f"{self.fail_to_pass_passed}/{self.fail_to_pass_total}",
            "regressions": self.pass_to_pass_regressed,
            "reason": self.reason,
        }


def score_instance(
    record: dict[str, Any],
    task: dict[str, Any],
    workdir: Path,
    runner: Any | None = None,
) -> InstanceVerdict:
    """Grade one run record against one task.

    `runner` is the command executor, injected so the scorer can be tested without Docker
    and so evalgate can supply its own container backend.
    """
    run = runner or _subprocess_runner
    instance_id = str(record.get("task_id", ""))
    fail_to_pass = _as_list(task.get("FAIL_TO_PASS", task.get("fail_to_pass", [])))
    pass_to_pass = _as_list(task.get("PASS_TO_PASS", task.get("pass_to_pass", [])))

    patch = str(record.get("patch", ""))
    if not patch.strip():
        return InstanceVerdict(
            instance_id, False, False, 0, len(fail_to_pass), reason="empty patch"
        )

    # Reset to base, then apply the agent's patch and the graded tests in that order.
    run(["git", "checkout", "--force", str(task.get("base_commit", ""))], workdir, APPLY_TIMEOUT_S)
    run(["git", "clean", "-fdx"], workdir, APPLY_TIMEOUT_S)

    if not _apply(patch, workdir, run):
        return InstanceVerdict(
            instance_id, False, False, 0, len(fail_to_pass), reason="patch did not apply"
        )

    test_patch = str(task.get("test_patch", ""))
    if test_patch.strip() and not _apply(test_patch, workdir, run):
        return InstanceVerdict(
            instance_id, False, True, 0, len(fail_to_pass), reason="test_patch did not apply"
        )

    outcomes = _run_tests(sorted(set(fail_to_pass) | set(pass_to_pass)), workdir, run)

    passed_targets = [test for test in fail_to_pass if outcomes.get(test) == "PASSED"]
    regressions = [test for test in pass_to_pass if outcomes.get(test) not in ("PASSED", None)]

    resolved = len(passed_targets) == len(fail_to_pass) and not regressions and bool(fail_to_pass)
    reason = ""
    if not fail_to_pass:
        reason = "instance declares no FAIL_TO_PASS tests"
    elif regressions:
        reason = f"{len(regressions)} PASS_TO_PASS regression(s)"
    elif not resolved:
        reason = f"{len(passed_targets)}/{len(fail_to_pass)} target tests passed"

    return InstanceVerdict(
        instance_id=instance_id,
        resolved=resolved,
        patch_applied=True,
        fail_to_pass_passed=len(passed_targets),
        fail_to_pass_total=len(fail_to_pass),
        pass_to_pass_regressed=regressions,
        reason=reason,
    )


def _apply(patch: str, workdir: Path, run: Any) -> bool:
    """Try progressively looser strip levels.

    Patches arrive from several agents with different path conventions; `-p1` is the
    common case, and falling back is cheaper than rejecting a correct fix on formatting.
    """
    patch_file = workdir / ".regin-patch.diff"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        for strip in ("-p1", "-p0"):
            result = run(
                ["git", "apply", "--whitespace=nowarn", strip, str(patch_file)],
                workdir,
                APPLY_TIMEOUT_S,
            )
            if result[0] == 0:
                return True
        return False
    finally:
        patch_file.unlink(missing_ok=True)


def _run_tests(tests: list[str], workdir: Path, run: Any) -> dict[str, str]:
    if not tests:
        return {}
    exit_code, output = run(
        ["python", "-m", "pytest", "-rA", "--tb=no", "-q", *tests], workdir, TEST_TIMEOUT_S
    )
    del exit_code  # a non-zero exit is expected when a graded test fails
    return {match.group(2): match.group(1) for match in RESULT_LINE.finditer(output)}


def _subprocess_runner(command: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout_s}s"
    return completed.returncode, completed.stdout + completed.stderr


def _as_list(value: Any) -> list[str]:
    """Upstream ships these as JSON strings in some exports and as lists in others."""
    if isinstance(value, str):
        return json.loads(value) if value.strip() else []
    return list(value or [])
