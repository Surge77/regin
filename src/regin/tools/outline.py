"""The `outline` tool — repository structure without reading whole files."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from regin.context.outline import outline_file, outline_repo
from regin.sandbox.paths import safe_path


class OutlineArgs(BaseModel):
    path: str = Field(
        default=".",
        description="A file to outline, or a directory to outline recursively",
    )
    pattern: str = Field(
        default="**/*.py",
        description="Glob applied when path is a directory",
    )


class OutlineTool:
    name = "outline"
    description = (
        "List the classes and function signatures in a file or directory, without reading "
        "the full contents. Use this to locate code before reading it."
    )
    args_model = OutlineArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, OutlineArgs)
        target = safe_path(self._root, args.path, "outline path")

        if target.is_file():
            return outline_file(target, target.relative_to(self._root).as_posix()).render()
        return outline_repo(target, args.pattern)
