"""Planning pass — think once before editing.

Phase 3.4, and the headline A/B candidate. The hypothesis is that one cheap planning turn
before any edit reduces flailing on multi-file tasks enough to pay for itself in turns and
cost. That is a hypothesis, not a fact, which is exactly why it ships behind a flag that
defaults to off: the baseline must stay unchanged until a sweep says otherwise.

Two knobs are worth A/B-ing independently — whether to plan at all, and at what effort.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regin.schemas import Message

if TYPE_CHECKING:
    from regin.config import Config
    from regin.providers.base import Provider

PLANNER_PROMPT = """You are planning a code change. You cannot edit anything yet.

Given the task, write a short plan:
1. What you need to find out first, and where you expect to find it.
2. The smallest change that could plausibly fix this.
3. How you will verify it — the exact command you expect to run.

Be concrete and brief. If the task is trivial, say so in one line and stop. Do not speculate
about code you have not seen; state what you would check instead.
"""

PLAN_PREAMBLE = (
    "Before starting, you wrote this plan. Follow it, and revise it if what you find "
    "contradicts it.\n\n"
)


class Planner:
    """Produces one plan turn that is injected before the agent's first action."""

    def __init__(self, provider: Provider, config: Config) -> None:
        self._provider = provider
        self._config = config

    async def plan(self, task: str) -> str:
        response = await self._provider.complete(
            messages=[
                Message(role="system", content=PLANNER_PROMPT),
                Message(role="user", content=task),
            ],
            tools=[],
            model=self._config.model.name,
            max_output_tokens=self._config.loop.planner_max_tokens,
            effort=self._config.loop.planner_effort,
        )
        return response.text.strip()

    async def seed(self, messages: list[Message], task: str) -> list[Message]:
        """Insert the plan as an assistant turn ahead of the agent's first move.

        An assistant turn rather than a system one: the plan is the agent's own reasoning,
        and presenting it as an instruction invites the model to follow it past the point
        where evidence says it is wrong.
        """
        if not self._config.loop.plan_first:
            return messages

        plan = await self.plan(task)
        if not plan:
            return messages

        return [*messages, Message(role="assistant", content=PLAN_PREAMBLE + plan)]
