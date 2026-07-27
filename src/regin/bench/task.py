"""SWE-bench task shapes.

Field names match the upstream dataset exactly so a raw `swe-bench-lite` JSONL export
loads without a translation step. `FAIL_TO_PASS` and `PASS_TO_PASS` keep their screaming
case for the same reason — renaming them would mean maintaining a mapping that silently
rots when the dataset changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# The instruction the agent receives. It deliberately does not mention the tests that
# grade it: an agent told which test to satisfy will special-case that test.
TASK_TEMPLATE = """Resolve the following issue in this repository.

<issue>
{problem_statement}
</issue>

Work directly in the checked-out repository. When you believe the issue is fixed, run the
project's tests to confirm, then stop and summarise the change you made.
"""


class Task(BaseModel):
    instance_id: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    environment_setup_commit: str = ""

    # The reference solution. Never shown to the agent — it is the answer key.
    patch: str = ""
    test_patch: str = ""

    fail_to_pass: list[str] = Field(default_factory=list, alias="FAIL_TO_PASS")
    pass_to_pass: list[str] = Field(default_factory=list, alias="PASS_TO_PASS")

    model_config = {"populate_by_name": True}

    @field_validator("fail_to_pass", "pass_to_pass", mode="before")
    @classmethod
    def _accept_json_encoded_lists(cls, value: Any) -> Any:
        """Upstream ships these as JSON strings in some exports and as lists in others."""
        if isinstance(value, str):
            return json.loads(value) if value.strip() else []
        return value

    def prompt(self) -> str:
        return TASK_TEMPLATE.format(problem_statement=self.problem_statement.strip())

    def docker_image(self) -> str:
        """The official per-instance evaluation image.

        Matching the upstream harness image matters: resolve-rate is only comparable to
        published numbers if the environment is the one those numbers were measured in.
        """
        return f"swebench/sweb.eval.x86_64.{self.instance_id.replace('__', '_1776_')}:latest"


def load_tasks(path: Path, limit: int | None = None, only: list[str] | None = None) -> list[Task]:
    """Read tasks from JSONL, preserving file order so a truncated run is reproducible."""
    wanted = set(only) if only else None
    tasks: list[Task] = []

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            task = Task.model_validate_json(line)
            if wanted is not None and task.instance_id not in wanted:
                continue
            tasks.append(task)
            if limit is not None and len(tasks) >= limit:
                break

    if wanted:
        missing = wanted - {task.instance_id for task in tasks}
        if missing:
            raise ValueError(f"instances not found in {path}: {', '.join(sorted(missing))}")

    return tasks
