"""Glob and grep: gitignore awareness, result caps, and the ripgrep JSON parser."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from regin.sandbox.paths import PathEscapeError
from regin.tools.search import (
    MAX_GLOB_RESULTS,
    GlobArgs,
    GlobTool,
    GrepArgs,
    GrepTool,
    _parse_ripgrep_json,
)

needs_ripgrep = pytest.mark.skipif(
    shutil.which("rg") is None, reason="ripgrep is not installed on this machine"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("SECRET_MARKER = 2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# notes\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.py").write_text("generated\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    return tmp_path


async def test_glob_finds_matching_files(repo: Path) -> None:
    result = await GlobTool(repo).run(GlobArgs(pattern="src/*.py"))
    assert "src/main.py" in result
    assert "src/util.py" in result


async def test_glob_excludes_gitignored_paths(repo: Path) -> None:
    result = await GlobTool(repo).run(GlobArgs(pattern="**/*.py"))
    assert "build/out.py" not in result
    assert "src/main.py" in result


async def test_glob_excludes_always_ignored_directories(repo: Path) -> None:
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "cached.py").write_text("x\n", encoding="utf-8")
    result = await GlobTool(repo).run(GlobArgs(pattern="**/*.py"))
    assert "__pycache__" not in result


async def test_glob_reports_no_matches_rather_than_failing(repo: Path) -> None:
    result = await GlobTool(repo).run(GlobArgs(pattern="**/*.rs"))
    assert "no files match" in result


async def test_glob_caps_results_and_says_so(tmp_path: Path) -> None:
    for index in range(MAX_GLOB_RESULTS + 25):
        (tmp_path / f"file{index:04d}.py").write_text("x\n", encoding="utf-8")
    result = await GlobTool(tmp_path).run(GlobArgs(pattern="*.py"))
    assert "narrow the pattern" in result
    assert len(result.splitlines()) <= MAX_GLOB_RESULTS + 1


async def test_glob_refuses_a_path_outside_the_root(repo: Path) -> None:
    with pytest.raises(PathEscapeError):
        await GlobTool(repo).run(GlobArgs(pattern="*.py", path="../.."))


@needs_ripgrep
async def test_grep_finds_a_match(repo: Path) -> None:
    result = await GrepTool(repo).run(GrepArgs(pattern="SECRET_MARKER"))
    assert "util.py" in result
    assert "SECRET_MARKER" in result


@needs_ripgrep
async def test_grep_reports_no_matches_as_a_normal_result(repo: Path) -> None:
    """ripgrep exits 1 when nothing matches; that is not a failure."""
    result = await GrepTool(repo).run(GrepArgs(pattern="zzz_does_not_exist_zzz"))
    assert "no matches" in result


@needs_ripgrep
async def test_grep_respects_a_glob_filter(repo: Path) -> None:
    result = await GrepTool(repo).run(GrepArgs(pattern="notes", glob="*.md"))
    assert "notes.md" in result


@needs_ripgrep
async def test_grep_refuses_a_path_outside_the_root(repo: Path) -> None:
    with pytest.raises(PathEscapeError):
        await GrepTool(repo).run(GrepArgs(pattern="x", path="../../etc"))


def test_ripgrep_parser_extracts_matches(tmp_path: Path) -> None:
    path = (tmp_path / "a.py").as_posix()
    stdout = (
        f'{{"type":"begin","data":{{"path":{{"text":"{path}"}}}}}}\n'
        f'{{"type":"match","data":{{"path":{{"text":"{path}"}},"line_number":7,'
        f'"lines":{{"text":"hit here\\n"}}}}}}\n'
        '{"type":"end","data":{}}\n'
    )
    assert _parse_ripgrep_json(stdout, tmp_path) == ["a.py:7: hit here"]


def test_ripgrep_parser_skips_malformed_lines(tmp_path: Path) -> None:
    """A truncated JSON line must not lose the matches around it."""
    path = (tmp_path / "a.py").as_posix()
    stdout = (
        "{not json\n"
        f'{{"type":"match","data":{{"path":{{"text":"{path}"}},"line_number":1,'
        f'"lines":{{"text":"kept\\n"}}}}}}\n'
    )
    assert _parse_ripgrep_json(stdout, tmp_path) == ["a.py:1: kept"]
