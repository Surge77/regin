"""Agents under test.

The point of the harness is that these are scored identically: same task set, same
checkout, same scorer, same cost accounting. Where a competitor reports cost differently,
the difference is recorded rather than smoothed over — a comparison built on estimates for
one agent and provider `usage` for another is not a comparison.

`claude-agent-sdk` appears here and only here. It is a benchmark competitor, never a
foundation: the control flow it owns is this project's deliverable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from regin.agent import build_tools
from regin.bench.task import Task
from regin.config import Config
from regin.loop import AgentLoop
from regin.permissions import Permissions
from regin.providers.anthropic import AnthropicProvider
from regin.sandbox.exec import DockerExecutor, Executor, LocalExecutor
from regin.schemas import Outcome, RunRecord, Usage, config_hash

COMPETITOR_TIMEOUT_S = 1800


class ReginAgent:
    """The agent this project ships, assembled exactly as `regin fix` assembles it."""

    name = "regin"

    def __init__(self, config: Config, api_key: str, use_docker: bool = True) -> None:
        self._config = config
        self._api_key = api_key
        self._use_docker = use_docker

    async def run(self, task: Task, workdir: Path) -> RunRecord:
        executor: Executor = (
            DockerExecutor(task.docker_image(), workdir)
            if self._use_docker
            else LocalExecutor(workdir)
        )

        # Inside a disposable container the container is the boundary, so the allowlist is
        # dropped — a repository's own test runner may legitimately invoke anything.
        registry = build_tools(
            workdir,
            self._config,
            executor=executor,
            allowlist=() if self._use_docker else None,
        )

        loop = AgentLoop(
            provider=AnthropicProvider(api_key=self._api_key),
            tools=registry,
            permissions=Permissions(self._config.permissions, auto_approve=True),
            config=self._config,
        )

        try:
            return await loop.run(task.prompt(), task_id=task.instance_id)
        finally:
            await executor.close()


class SubprocessAgent:
    """Shared shape for competitors driven through their CLI.

    Cost is read from whatever the tool reports; when it reports nothing, the record says
    zero and the scorecard flags it rather than quietly substituting an estimate.
    """

    name = "subprocess"
    command_template: list[str] = []

    def __init__(self, config: Config, timeout_s: int = COMPETITOR_TIMEOUT_S) -> None:
        self._config = config
        self._timeout_s = timeout_s

    def _command(self, task: Task) -> list[str]:
        raise NotImplementedError

    def _parse_cost(self, stdout: str) -> tuple[float, Usage]:
        return 0.0, Usage()

    async def run(self, task: Task, workdir: Path) -> RunRecord:
        record = RunRecord(
            run_id=uuid.uuid4().hex[:12],
            task_id=task.instance_id,
            agent=self.name,
            model=self._config.model.name,
            config_hash=config_hash(self._config.hashable()),
        )
        started = time.monotonic()

        command = self._command(task)
        if shutil.which(command[0]) is None:
            record.outcome = Outcome.ERROR
            record.error = f"{command[0]} is not installed; competitor skipped"
            return record

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self._timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            record.outcome = Outcome.TURN_LIMIT
            record.error = f"competitor exceeded {self._timeout_s}s"
            record.latency_ms = int((time.monotonic() - started) * 1000)
            return record

        output = stdout.decode(errors="replace")
        record.cost_usd, record.usage = self._parse_cost(output)
        record.outcome = Outcome.RESOLVED if proc.returncode == 0 else Outcome.UNRESOLVED
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.finished_at = datetime.now(UTC)
        return record


class ClaudeCodeAgent(SubprocessAgent):
    """Claude Code via its CLI, in headless mode.

    Used strictly as a competitor. `--output-format json` gives real usage and cost, so
    this row of the scorecard is comparable to regin's rather than estimated.
    """

    name = "claude-code"

    def _command(self, task: Task) -> list[str]:
        return [
            "claude",
            "-p",
            task.prompt(),
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            self._config.model.name,
        ]

    def _parse_cost(self, stdout: str) -> tuple[float, Usage]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return 0.0, Usage()

        usage = payload.get("usage", {})
        return float(payload.get("total_cost_usd", 0.0)), Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        )


class AiderAgent(SubprocessAgent):
    """Aider in non-interactive mode.

    Aider prints cost as prose rather than structured output, so its cost column is
    reported as unavailable rather than parsed out of a log line that changes between
    releases. Resolve-rate, latency, and the patch are still fully comparable.
    """

    name = "aider"

    def _command(self, task: Task) -> list[str]:
        return [
            "aider",
            "--yes",
            "--no-auto-commits",
            "--no-check-update",
            "--model",
            self._config.model.name,
            "--message",
            task.prompt(),
        ]
