"""Filesystem tools. Every path goes through safe_path().

`edit_file` uses anchored search/replace rather than model-generated unified diffs:
models emit invalid hunk headers often enough to stall a run, and a failed anchor
match is a clean, recoverable error that can be fed straight back to the model.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from regin.sandbox.paths import safe_path


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path relative to the working directory")


class WriteFileArgs(BaseModel):
    path: str = Field(description="File path relative to the working directory")
    content: str = Field(description="Complete file content")


class EditFileArgs(BaseModel):
    path: str = Field(description="File path relative to the working directory")
    old: str = Field(description="Exact existing text to replace. Must match once.")
    new: str = Field(description="Replacement text")


class EditMatchError(ValueError):
    """Anchor text did not match exactly once — recoverable, report back to the model."""


class ReadFileTool:
    name = "read_file"
    description = "Read a file's contents."
    args_model = ReadFileArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, ReadFileArgs)
        return safe_path(self._root, args.path, "read path").read_text(encoding="utf-8")


class WriteFileTool:
    name = "write_file"
    description = "Write a file, creating parent directories as needed."
    args_model = WriteFileArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, WriteFileArgs)
        target = safe_path(self._root, args.path, "write path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.content, encoding="utf-8")
        return f"wrote {args.path} ({len(args.content)} chars)"


class EditFileTool:
    name = "edit_file"
    description = (
        "Replace an exact block of text in a file. The `old` text must appear exactly once."
    )
    args_model = EditFileArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, EditFileArgs)
        target = safe_path(self._root, args.path, "edit path")
        source = target.read_text(encoding="utf-8")

        occurrences = source.count(args.old)
        if occurrences == 0:
            raise EditMatchError(
                f"anchor text not found in {args.path}; re-read the file and quote it exactly"
            )
        if occurrences > 1:
            raise EditMatchError(
                f"anchor text matched {occurrences} times in {args.path}; include more "
                "surrounding context to make it unique"
            )

        target.write_text(source.replace(args.old, args.new, 1), encoding="utf-8")
        return f"edited {args.path}"
