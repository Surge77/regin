"""Repository outline — structure instead of file dumps."""

from __future__ import annotations

from pathlib import Path

from regin.context.outline import outline_file, outline_repo
from regin.tools.outline import OutlineArgs, OutlineTool

SOURCE = '''\
"""Module docstring."""

import os

CONSTANT = 1


def top_level(a, b=2, *args, **kwargs):
    def nested_helper():
        return 1

    return nested_helper()


async def fetch(url):
    return url


class Widget:
    """A widget."""

    def render(self, target):
        return target

    async def refresh(self):
        return None
'''


def test_lists_top_level_functions_with_parameters(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(SOURCE, encoding="utf-8")

    rendered = outline_file(path).render()

    assert "def top_level(a, b, *args, **kwargs)" in rendered
    assert "async def fetch(url)" in rendered


def test_omits_nested_helpers(tmp_path: Path) -> None:
    """Closures are implementation detail — including them restores the noise the outline
    exists to remove."""
    path = tmp_path / "m.py"
    path.write_text(SOURCE, encoding="utf-8")

    assert "nested_helper" not in outline_file(path).render()


def test_lists_classes_with_their_methods(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(SOURCE, encoding="utf-8")
    rendered = outline_file(path).render()

    assert "class Widget" in rendered
    assert "def render(self, target)" in rendered
    assert "async def refresh(self)" in rendered


def test_an_outline_is_much_smaller_than_the_source(tmp_path: Path) -> None:
    """The entire point: structure at a fraction of the token cost."""
    path = tmp_path / "m.py"
    path.write_text(SOURCE * 20, encoding="utf-8")

    assert len(outline_file(path).render()) < len(path.read_text(encoding="utf-8")) / 3


def test_reports_a_syntax_error_rather_than_failing(tmp_path: Path) -> None:
    """The agent may have just caused it, so it is worth surfacing precisely."""
    path = tmp_path / "broken.py"
    path.write_text("def oops(\n", encoding="utf-8")

    rendered = outline_file(path).render()
    assert "syntax error" in rendered


def test_falls_back_to_a_signature_scan_for_other_languages(tmp_path: Path) -> None:
    path = tmp_path / "app.ts"
    path.write_text(
        "export function handler(req) {}\nclass Service {}\ninterface Props {}\n",
        encoding="utf-8",
    )

    rendered = outline_file(path).render()
    assert "function handler" in rendered
    assert "class Service" in rendered


def test_reports_an_unreadable_file_rather_than_raising(tmp_path: Path) -> None:
    path = tmp_path / "binary.py"
    path.write_bytes(b"\xff\xfe\x00\x00invalid")

    assert "unreadable" in outline_file(path).render()


def test_outlines_a_directory_respecting_gitignore(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("def kept(): pass\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("def generated(): pass\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")

    rendered = outline_repo(tmp_path)

    assert "kept" in rendered
    assert "generated" not in rendered


def test_caps_the_number_of_files_and_says_so(tmp_path: Path) -> None:
    """Past a few hundred files the outline becomes the context problem it solves."""
    for i in range(12):
        (tmp_path / f"m{i}.py").write_text("def f(): pass\n", encoding="utf-8")

    rendered = outline_repo(tmp_path, max_files=5)
    assert "narrow the pattern" in rendered


def test_reports_no_matches_rather_than_an_empty_string(tmp_path: Path) -> None:
    assert "no files match" in outline_repo(tmp_path, pattern="**/*.rs")


async def test_tool_outlines_a_single_file(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    result = await OutlineTool(tmp_path).run(OutlineArgs(path="m.py"))
    assert "class Widget" in result


async def test_tool_outlines_a_directory(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    result = await OutlineTool(tmp_path).run(OutlineArgs(path="."))
    assert "top_level" in result


async def test_tool_refuses_a_path_outside_the_root(tmp_path: Path) -> None:
    import pytest

    from regin.sandbox.paths import PathEscapeError

    with pytest.raises(PathEscapeError):
        await OutlineTool(tmp_path).run(OutlineArgs(path="../../etc"))
