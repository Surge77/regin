from pathlib import Path

import pytest

from regin.tools.base import truncate
from regin.tools.fs import (
    EditFileArgs,
    EditFileTool,
    EditMatchError,
    ReadFileArgs,
    ReadFileTool,
    WriteFileArgs,
    WriteFileTool,
)


async def test_reads_a_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('hi')", encoding="utf-8")
    result = await ReadFileTool(tmp_path).run(ReadFileArgs(path="a.py"))
    assert result == "print('hi')"


async def test_writes_a_file_creating_parents(tmp_path: Path) -> None:
    await WriteFileTool(tmp_path).run(WriteFileArgs(path="pkg/mod.py", content="x = 1"))
    assert (tmp_path / "pkg" / "mod.py").read_text(encoding="utf-8") == "x = 1"


async def test_edits_a_unique_anchor(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    await EditFileTool(tmp_path).run(EditFileArgs(path="a.py", old="y = 2", new="y = 3"))
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


async def test_rejects_an_anchor_that_is_not_found(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(EditMatchError, match="not found"):
        await EditFileTool(tmp_path).run(EditFileArgs(path="a.py", old="z = 9", new="z = 0"))


async def test_rejects_an_ambiguous_anchor(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(EditMatchError, match="matched 2 times"):
        await EditFileTool(tmp_path).run(EditFileArgs(path="a.py", old="x = 1", new="x = 2"))


def test_truncate_leaves_short_output_alone() -> None:
    assert truncate("abc", 100) == "abc"


def test_truncate_marks_omitted_chars() -> None:
    result = truncate("a" * 500, 100)
    assert "omitted" in result
    assert len(result) < 500
