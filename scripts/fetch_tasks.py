"""Download a SWE-bench split to JSONL.

Kept as a script rather than a CLI command: it needs `datasets`, which is a heavy
dependency the agent itself has no use for, and it runs once per machine.

    pip install datasets
    python scripts/fetch_tasks.py lite     # 300 instances, start here for cost
    python scripts/fetch_tasks.py verified # 500 instances, report on this
"""

from __future__ import annotations

import sys
from pathlib import Path

SPLITS = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "verified": "princeton-nlp/SWE-bench_Verified",
    "full": "princeton-nlp/SWE-bench",
}

OUT_DIR = Path("tasks")


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "lite"
    if name not in SPLITS:
        print(f"unknown split {name!r}; choose from {', '.join(SPLITS)}", file=sys.stderr)
        return 2

    try:
        from datasets import load_dataset
    except ImportError:
        print("`datasets` is not installed. Run: pip install datasets", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUT_DIR / f"swe-bench-{name}.jsonl"

    dataset = load_dataset(SPLITS[name], split="test")
    dataset.to_json(str(destination))

    # The loader reads upstream field names as-is, so no conversion step is needed —
    # but say so out loud, because a silent format assumption is worth verifying once.
    print(f"wrote {destination} ({len(dataset)} instances)")
    print("verify with: python scripts/check_tasks.py", destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
