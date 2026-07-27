"""Validate a task file before spending anything on it.

Every check here catches a failure that would otherwise surface partway through a paid
sweep, when the money is already gone.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from regin.bench.task import load_tasks


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python scripts/check_tasks.py <tasks.jsonl>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    try:
        tasks = load_tasks(path)
    except Exception as exc:
        print(f"failed to parse: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"{len(tasks)} instances parsed from {path}")

    problems: list[str] = []

    # An instance with no FAIL_TO_PASS can never resolve, so it is dead weight in the
    # denominator — it drags the reported rate down while measuring nothing.
    no_targets = [t.instance_id for t in tasks if not t.fail_to_pass]
    if no_targets:
        problems.append(f"{len(no_targets)} instance(s) declare no FAIL_TO_PASS tests")

    # The runner clones by repo + base_commit; either missing means a guaranteed skip.
    incomplete = [t.instance_id for t in tasks if not t.repo or not t.base_commit]
    if incomplete:
        problems.append(f"{len(incomplete)} instance(s) missing repo or base_commit")

    # The agent is graded on the problem statement alone. An empty one is unanswerable.
    empty = [t.instance_id for t in tasks if not t.problem_statement.strip()]
    if empty:
        problems.append(f"{len(empty)} instance(s) have an empty problem statement")

    duplicates = [i for i, n in Counter(t.instance_id for t in tasks).items() if n > 1]
    if duplicates:
        problems.append(f"{len(duplicates)} duplicate instance_id(s)")

    repos = Counter(t.repo for t in tasks)
    print(f"{len(repos)} distinct repositories; most common:")
    for repo, count in repos.most_common(5):
        print(f"  {count:>4}  {repo}")

    print("\nsample prompt (what the agent actually sees):")
    print("  " + "\n  ".join(tasks[0].prompt().splitlines()[:6]))

    # Cheap guard against the worst possible leak: the answer key in the prompt.
    if tasks[0].patch and tasks[0].patch in tasks[0].prompt():
        problems.append("the reference patch is leaking into the prompt")

    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print("\nno problems found — safe to sweep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
