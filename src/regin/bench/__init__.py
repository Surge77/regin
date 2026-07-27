"""The benchmark loop — the measurement half of the project.

`regin` produces JSONL run records; `evalgate` consumes them and decides what resolved.
Neither imports the other's internals, so a scoring change cannot silently alter the
agent and an agent change cannot silently alter the scorer.
"""

from regin.bench.runner import BenchAgent, SweepConfig, SweepResult, run_sweep
from regin.bench.task import Task, load_tasks

__all__ = ["BenchAgent", "SweepConfig", "SweepResult", "Task", "load_tasks", "run_sweep"]
