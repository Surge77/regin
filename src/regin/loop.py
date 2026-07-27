"""The agent loop. This file is the deliverable — keep it under 300 lines.

If it grows past that, compaction or permission logic has leaked in. Extract, don't expand.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from regin.config import Config
from regin.context.compaction import Compactor
from regin.context.planning import Planner
from regin.hooks import HookRegistry, HookVeto
from regin.permissions import PermissionError_, Permissions
from regin.providers.base import Provider, cost_usd
from regin.schemas import (
    Message,
    Outcome,
    RunRecord,
    ToolCall,
    ToolResult,
    Usage,
    config_hash,
)
from regin.tools.base import ToolRegistry, truncate
from regin.ui import Renderer

SYSTEM_PROMPT = """You are regin, a coding agent working in a real repository.

Work in small, verified steps:
1. Read before you edit. Never guess a file's contents.
2. Make one focused change at a time.
3. Run the tests after every change and read the failure output.
4. Iterate until the tests pass.

When editing, quote the existing text exactly — anchors must match once.
Stop and explain if you cannot make progress; do not loop on a failing approach.
"""


class AgentLoop:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        permissions: Permissions,
        config: Config,
        compactor: Compactor | None = None,
        renderer: Renderer | None = None,
        planner: Planner | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._permissions = permissions
        self._config = config
        self._compactor = compactor or Compactor(provider, config)
        self._planner = planner or Planner(provider, config)
        self._hooks = hooks or HookRegistry()
        self._renderer = renderer or Renderer(quiet=True)
        self.messages: list[Message] = []

    async def run(
        self,
        task: str,
        task_id: str = "adhoc",
        resume_from: list[Message] | None = None,
    ) -> RunRecord:
        started = time.monotonic()
        record = RunRecord(
            run_id=uuid.uuid4().hex[:12],
            task_id=task_id,
            model=self._config.model.name,
            config_hash=config_hash(self._config.hashable()),
        )

        if resume_from is not None:
            # A resumed run already has a plan and a system prompt in its history.
            messages = list(resume_from)
        else:
            messages = [
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=task),
            ]
            # No-op unless loop.plan_first is enabled — see context/planning.py for why
            # this ships off by default.
            messages = await self._planner.seed(messages, task)
        total = Usage()

        while True:
            if record.turns >= self._config.loop.max_turns:
                record.outcome = Outcome.TURN_LIMIT
                break
            if record.cost_usd >= self._config.loop.max_run_cost_usd:
                record.outcome = Outcome.BUDGET_EXCEEDED
                break

            messages = await self._compactor.maybe_compact(messages, total.input_tokens)

            try:
                response = await self._provider.complete(
                    messages=messages,
                    tools=self._tools.schemas(),
                    model=self._config.model.name,
                    max_output_tokens=self._config.model.max_output_tokens,
                    effort=self._config.model.effort,
                )
            except Exception as exc:  # provider-level failure ends the run
                record.outcome = Outcome.ERROR
                record.error = f"{type(exc).__name__}: {exc}"
                break

            record.turns += 1
            self._renderer.end_text()
            total = total + response.usage
            record.cost_usd = cost_usd(
                self._config.model.name,
                total.input_tokens,
                total.output_tokens,
                total.cache_read_tokens,
                total.cache_write_tokens,
            )

            messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls,
                    provider_blocks=response.provider_blocks,
                )
            )

            # No tool calls means the model considers the task finished.
            if not response.tool_calls:
                record.outcome = Outcome.RESOLVED
                record.summary = response.text
                break

            self._renderer.turn(record.turns, record.cost_usd)
            results = [await self._execute(call) for call in response.tool_calls]
            messages.append(Message(role="tool", tool_results=results))

        # Exposed so the caller can persist a resumable session without the loop needing
        # to know where sessions are stored.
        self.messages = messages
        record.usage = total
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.finished_at = datetime.now(UTC)
        return record

    async def _execute(self, call: ToolCall) -> ToolResult:
        """Run one tool call. Every failure mode returns to the model as text, not an exception —
        a recoverable error the model can react to is worth more than a dead run."""
        self._renderer.tool_call(call)
        result = await self._dispatch(call)
        self._renderer.tool_result(result)
        return result

    async def _dispatch(self, call: ToolCall) -> ToolResult:
        try:
            tool = self._tools.get(call.name)
        except KeyError as exc:
            return ToolResult(call_id=call.id, content=str(exc), is_error=True)

        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(call_id=call.id, content=f"invalid arguments: {exc}", is_error=True)

        try:
            self._permissions.check(call.name, summarise_call(call))
            # Inside the permission gate, never around it: a hook can veto, never approve.
            await self._hooks.before(call)
        except (PermissionError_, HookVeto) as exc:
            return ToolResult(call_id=call.id, content=str(exc), is_error=True)

        try:
            output = await tool.run(args)
        except Exception as exc:
            result = ToolResult(
                call_id=call.id, content=f"{type(exc).__name__}: {exc}", is_error=True
            )
        else:
            result = ToolResult(
                call_id=call.id,
                content=truncate(output, self._config.tools.max_tool_output_chars),
            )

        await self._hooks.after(call, result)
        return result


def summarise_call(call: ToolCall) -> str:
    parts = [f"{k}={str(v)[:60]}" for k, v in call.arguments.items()]
    return f"{call.name}({', '.join(parts)})"
