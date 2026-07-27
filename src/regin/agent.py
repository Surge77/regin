"""Assembly. One place that wires provider + tools + permissions into a loop.

The CLI and the benchmark runner must build the agent identically, or a benchmark number
describes a configuration no user ever runs.
"""

from __future__ import annotations

from pathlib import Path

from regin.config import Config
from regin.loop import AgentLoop
from regin.permissions import ConsolePrompter, Permissions
from regin.providers.anthropic import AnthropicProvider
from regin.providers.base import Provider
from regin.providers.retry import RetryingProvider
from regin.sandbox.exec import Executor
from regin.tools.base import ToolRegistry
from regin.tools.fs import EditFileTool, ReadFileTool, WriteFileTool
from regin.tools.outline import OutlineTool
from regin.tools.search import GlobTool, GrepTool
from regin.tools.shell import BashTool
from regin.tools.subagent import SubagentTool
from regin.ui import Renderer


class MissingAPIKeyError(RuntimeError):
    """No credential for the configured provider."""


def build_tools(
    root: Path,
    config: Config,
    executor: Executor | None = None,
    allowlist: tuple[str, ...] | None = None,
    provider: Provider | None = None,
) -> ToolRegistry:
    """The tool surface. Order is stable so the cached prompt prefix stays valid.

    `executor` routes `bash` into a container for benchmark runs. `allowlist` overrides the
    configured one — a sweep passes an empty tuple, because inside a disposable container
    the container is the boundary and a repository's own test runner may invoke anything.
    """
    registry = ToolRegistry()
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(GlobTool(root))
    registry.register(GrepTool(root))
    if config.loop.outline_tool:
        registry.register(OutlineTool(root))
    if config.loop.subagent_tool and provider is not None:
        registry.register(SubagentTool(root, provider, config))
    registry.register(
        BashTool(
            root,
            timeout_s=config.tools.bash_timeout_s,
            allowlist=tuple(config.tools.bash_allowlist) if allowlist is None else allowlist,
            max_output_chars=config.tools.max_tool_output_chars,
            executor=executor,
        )
    )
    return registry


def build_provider(config: Config, api_key: str, renderer: Renderer) -> Provider:
    if config.model.provider != "anthropic":
        raise ValueError(f"unsupported provider: {config.model.provider}")
    if not api_key:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Put it in .env or export it before running."
        )

    provider = AnthropicProvider(api_key=api_key, on_text=renderer.text)
    # Retry wraps the provider rather than living in the loop, so the planner and the
    # compactor inherit it too. A rate limit at instance 200 of a sweep would otherwise be
    # recorded as an agent failure, which is a lie about the agent.
    return RetryingProvider(provider, max_attempts=config.loop.retry_attempts)


def build_agent(
    root: Path,
    config: Config,
    api_key: str,
    auto_approve: bool = False,
    quiet: bool = False,
) -> tuple[AgentLoop, Renderer]:
    renderer = Renderer(quiet=quiet)
    provider = build_provider(config, api_key, renderer)
    loop = AgentLoop(
        provider=provider,
        tools=build_tools(root, config, provider=provider),
        permissions=Permissions(
            config.permissions,
            prompter=None if quiet else ConsolePrompter(),
            auto_approve=auto_approve,
        ),
        config=config,
        renderer=renderer,
    )
    return loop, renderer
