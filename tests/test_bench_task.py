"""Task loading. Field names mirror the upstream SWE-bench export exactly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from regin.bench.task import Task, load_tasks


def _instance(instance_id: str, **overrides: object) -> str:
    payload: dict[str, object] = {
        "instance_id": instance_id,
        "repo": "psf/requests",
        "base_commit": "abc123",
        "problem_statement": "Something is broken.",
        "patch": "diff --git a/x b/x",
        "test_patch": "diff --git a/test_x b/test_x",
        "FAIL_TO_PASS": ["tests/test_x.py::test_one"],
        "PASS_TO_PASS": ["tests/test_x.py::test_two"],
    }
    payload.update(overrides)
    return json.dumps(payload) + "\n"


@pytest.fixture
def tasks_file(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.jsonl"
    path.write_text("".join(_instance(f"repo__issue-{i}") for i in range(5)), encoding="utf-8")
    return path


def test_loads_upstream_field_names(tasks_file: Path) -> None:
    tasks = load_tasks(tasks_file)
    assert len(tasks) == 5
    assert tasks[0].fail_to_pass == ["tests/test_x.py::test_one"]
    assert tasks[0].pass_to_pass == ["tests/test_x.py::test_two"]


def test_accepts_json_encoded_test_lists(tmp_path: Path) -> None:
    """Some exports ship these as JSON strings rather than arrays."""
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        _instance("a__b-1", FAIL_TO_PASS='["tests/test_x.py::test_one"]', PASS_TO_PASS="[]"),
        encoding="utf-8",
    )
    assert load_tasks(path)[0].fail_to_pass == ["tests/test_x.py::test_one"]


def test_limit_preserves_file_order(tasks_file: Path) -> None:
    """A truncated run must be reproducible, so order cannot depend on iteration chance."""
    assert [t.instance_id for t in load_tasks(tasks_file, limit=2)] == [
        "repo__issue-0",
        "repo__issue-1",
    ]


def test_selects_specific_instances(tasks_file: Path) -> None:
    tasks = load_tasks(tasks_file, only=["repo__issue-3"])
    assert [t.instance_id for t in tasks] == ["repo__issue-3"]


def test_raises_when_a_requested_instance_is_absent(tasks_file: Path) -> None:
    """Silently returning fewer tasks would quietly change the denominator."""
    with pytest.raises(ValueError, match="not found"):
        load_tasks(tasks_file, only=["nope__missing-1"])


def test_prompt_withholds_the_grading_tests() -> None:
    """An agent told which test grades it will special-case that test."""
    task = Task(
        instance_id="a__b-1",
        problem_statement="Fix the parser.",
        FAIL_TO_PASS=["tests/test_parser.py::test_secret"],
        patch="the answer key",
    )
    prompt = task.prompt()
    assert "Fix the parser." in prompt
    assert "test_secret" not in prompt
    assert "the answer key" not in prompt


def test_docker_image_matches_the_official_harness_naming() -> None:
    task = Task(instance_id="django__django-11099")
    assert task.docker_image() == "swebench/sweb.eval.x86_64.django_1776_django-11099:latest"
