"""Configuration: regin.toml plus REGIN_* env overrides, validated at startup."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from regin.tools.shell import DEFAULT_ALLOWLIST

DEFAULT_CONFIG_PATH = Path("regin.toml")


class ModelConfig(BaseModel):
    provider: str = "anthropic"
    name: str = "claude-sonnet-5"
    max_output_tokens: int = 16_000

    # Depth control. Sampling parameters (temperature / top_p / top_k) are rejected with
    # a 400 on the Claude 5 family, so `effort` is the only knob here.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"


class LoopConfig(BaseModel):
    max_turns: int = 40
    max_run_cost_usd: float = 2.00
    context_limit_tokens: int = 180_000
    # Fraction of context_limit_tokens at which the loop compacts rather than stalling.
    compact_at: float = 0.75


class ToolsConfig(BaseModel):
    bash_timeout_s: int = 120
    max_tool_output_chars: int = 30_000
    # Empty means unrestricted, which is only correct when a container is the boundary.
    bash_allowlist: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWLIST))


class TraceConfig(BaseModel):
    db_path: Path = Path("runs/regin.db")
    jsonl_path: Path = Path("runs/records.jsonl")


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""


class Config(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    permissions: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        target = path or DEFAULT_CONFIG_PATH
        if not target.exists():
            return cls()
        raw: dict[str, Any] = tomllib.loads(target.read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    def hashable(self) -> dict[str, Any]:
        """The subset that affects results — what config_hash is computed over."""
        return {
            "model": self.model.model_dump(),
            "loop": self.loop.model_dump(),
            "tools": self.tools.model_dump(),
        }
