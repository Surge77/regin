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
from regin.tools.base import ToolRegistry
from regin.tools.fs import EditFileTool, ReadFileTool, WriteFileTool
from regin.tools.search import GlobTool, GrepTool
from regin.tools.shell import BashTool
from regin.ui import Renderer


class MissingAPIKeyError(RuntimeError):
    """No credential for the configured provider."""


def build_tools(root: Path, config: Config) -> ToolRegistry:
    """The tool surface. Order is stable so the cached prompt prefix stays valid."""
    registry = ToolRegistry()
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(GlobTool(root))
    registry.register(GrepTool(root))
    registry.register(
        BashTool(
            root,
            timeout_s=config.tools.bash_timeout_s,
            allowlist=tuple(config.tools.bash_allowlist),
            max_output_chars=config.tools.max_tool_output_chars,
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
    return AnthropicProvider(api_key=api_key, on_text=renderer.text)


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
        tools=build_tools(root, config),
        permissions=Permissions(
            config.permissions,
            prompter=None if quiet else ConsolePrompter(),
            auto_approve=auto_approve,
        ),
        config=config,
        renderer=renderer,
    )
    return loop, renderer
