"""Per-instance repository checkouts.

The agent sees the repository at `base_commit` and nothing else. In particular it never
sees `test_patch` — the tests that grade it are applied at evaluation time, after the
agent has stopped. An agent shown its grading tests will special-case them, and the
resulting resolve-rate measures nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

CLONE_TIMEOUT_S = 600
GIT_TIMEOUT_S = 120


class WorkspaceError(RuntimeError):
    """Checkout failed. The instance is skipped rather than scored as unresolved —
    an environment failure is not an agent failure and must not pollute the number."""


async def prepare_workspace(repo: str, base_commit: str, dest: Path, cache_dir: Path) -> Path:
    """Materialise `repo` at `base_commit` under `dest`.

    Clones go through a local bare mirror: a 500-task sweep touches the same handful of
    repositories many times, and re-cloning each from GitHub would dominate wall-clock and
    invite rate limiting.
    """
    mirror = await _ensure_mirror(repo, cache_dir)
    dest.mkdir(parents=True, exist_ok=True)

    await _git(["clone", "--shared", "--no-checkout", str(mirror), str(dest)], cwd=dest.parent)
    await _git(["checkout", "--force", base_commit], cwd=dest)
    return dest


async def _ensure_mirror(repo: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    mirror = cache_dir / f"{repo.replace('/', '__')}.git"

    if mirror.exists():
        await _git(["remote", "update", "--prune"], cwd=mirror, timeout_s=CLONE_TIMEOUT_S)
        return mirror

    await _git(
        ["clone", "--mirror", f"https://github.com/{repo}.git", str(mirror)],
        cwd=cache_dir,
        timeout_s=CLONE_TIMEOUT_S,
    )
    return mirror


async def extract_patch(workdir: Path) -> str:
    """The agent's diff against the base commit — what the scorer applies and grades.

    Untracked files are staged first, otherwise a fix delivered as a new file silently
    produces an empty patch and scores as unresolved for the wrong reason.
    """
    await _git(["add", "-A"], cwd=workdir)
    return await _git(["diff", "--cached", "--no-color"], cwd=workdir)


async def _git(args: list[str], cwd: Path, timeout_s: int = GIT_TIMEOUT_S) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise WorkspaceError(f"git {args[0]} timed out after {timeout_s}s") from exc

    if proc.returncode != 0:
        raise WorkspaceError(f"git {args[0]} failed: {stderr.decode(errors='replace').strip()}")
    return stdout.decode(errors="replace")
