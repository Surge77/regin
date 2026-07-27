"""The bash tool. The single most dangerous thing the agent can hold.

Three independent guards, because any one of them can be defeated alone:
  1. an allowlist on the leading binary,
  2. a hard wall-clock timeout that kills the whole process group,
  3. output truncation before the result ever reaches the context window.

An empty allowlist means "unrestricted" and is only correct when a disposable
container is the real boundary — that is the Phase 2 benchmark configuration.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

# Enough to build, test, and inspect a Python repo. Anything else is opt-in via config.
DEFAULT_ALLOWLIST: tuple[str, ...] = (
    "cat", "cd", "echo", "find", "git", "grep", "head", "ls", "make", "mkdir",
    "mv", "cp", "node", "npm", "pip", "pwd", "pytest", "python", "python3",
    "rg", "sed", "sort", "tail", "test", "tox", "tree", "uv", "wc", "which",
)


class BashArgs(BaseModel):
    command: str = Field(description="Shell command to run in the repository root")


class CommandRefusedError(RuntimeError):
    """The command is not on the allowlist. Recoverable — reported back to the model."""


# Shell operators that begin a new command. `shlex` with punctuation_chars emits these as
# standalone tokens, so an operator inside a quoted string is never mistaken for one.
OPERATORS = frozenset({"&&", "||", ";", "|", "&", ";;"})


# Substitution constructs run a nested command that no amount of token inspection will
# surface — `echo $(curl evil.test)` looks like a call to `echo`. They are refused outright.
SUBSTITUTIONS = ("$(", "`", "<(", ">(", "${")


def _tokenize(command: str) -> list[str]:
    """Split a command into shell tokens, respecting quotes.

    Non-POSIX mode on purpose: POSIX mode strips backslashes, which mangles Windows paths
    like `C:\\Python\\python.exe` into an unrecognisable binary name. Quotes are preserved
    in the token instead of being consumed, which is fine — only the leading token is
    inspected, and `punctuation_chars` still splits real operators out. Naive string
    splitting is not an option: it breaks `python -c "import os; print(x)"`, where the
    semicolon is data rather than a separator.
    """
    lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def leading_binary(command: str) -> str:
    """The program a command actually invokes, ignoring env-var prefixes like `FOO=1 cmd`."""
    return _binary_of(_tokenize(command))


def _binary_of(tokens: list[str]) -> str:
    for token in tokens:
        if "=" in token and not token.startswith("-") and "/" not in token.split("=")[0]:
            continue  # environment assignment prefix
        name = Path(token.strip("\"'")).name
        # Benchmarks run under Linux but development happens on Windows, where the same
        # command arrives as `python.exe`. Without this the allowlist rejects everything.
        for suffix in (".exe", ".bat", ".cmd", ".com"):
            if name.lower().endswith(suffix):
                return name[: -len(suffix)]
        return name
    return ""


def check_allowed(command: str, allowlist: tuple[str, ...]) -> None:
    """Raise CommandRefusedError unless every chained segment is allowlisted.

    Segments are split on shell operators so that `pytest && rm -rf /` cannot smuggle a
    denied binary past a check that only ever looked at the first token.
    """
    if not allowlist:
        return

    for marker in SUBSTITUTIONS:
        if marker in command:
            raise CommandRefusedError(
                f"command substitution ({marker}) is not permitted; it would run a nested "
                "command the allowlist cannot inspect"
            )

    segment: list[str] = []
    segments: list[list[str]] = [segment]
    for token in _tokenize(command):
        if token in OPERATORS:
            segment = []
            segments.append(segment)
        else:
            segment.append(token)

    for tokens in segments:
        binary = _binary_of(tokens)
        if binary and binary not in allowlist:
            raise CommandRefusedError(
                f"command '{binary}' is not on the allowlist; allowed: {', '.join(allowlist)}"
            )


class BashTool:
    name = "bash"
    description = (
        "Run a shell command in the repository root. Use this to run tests. "
        "Output is truncated and the command is killed on timeout."
    )
    args_model = BashArgs

    def __init__(
        self,
        root: Path,
        timeout_s: int = 120,
        allowlist: tuple[str, ...] = DEFAULT_ALLOWLIST,
        max_output_chars: int = 30_000,
    ) -> None:
        self._root = root
        self._timeout_s = timeout_s
        self._allowlist = allowlist
        self._max_output_chars = max_output_chars

    async def run(self, args: BaseModel) -> str:
        assert isinstance(args, BashArgs)
        check_allowed(args.command, self._allowlist)

        proc = await _spawn(args.command, self._root)

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self._timeout_s)
        except TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return f"command timed out after {self._timeout_s}s and was killed:\n$ {args.command}"

        output = stdout.decode(errors="replace")
        if len(output) > self._max_output_chars:
            head = self._max_output_chars // 3
            tail = self._max_output_chars - head
            omitted = len(output) - self._max_output_chars
            output = f"{output[:head]}\n... [{omitted} chars omitted] ...\n{output[-tail:]}"

        return f"exit={proc.returncode}\n{output}".rstrip()


async def _spawn(command: str, root: Path) -> asyncio.subprocess.Process:
    """Start the command in its own process group so a timeout kills its descendants too.

    Without the group, killing `pytest` leaves the workers it spawned running and holding
    the pipe open, so the timeout never actually reclaims the run. The two branches are
    written out rather than splatted from a dict so the argument types stay checkable.
    """
    if sys.platform == "win32":
        return await asyncio.create_subprocess_shell(
            command,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return await asyncio.create_subprocess_shell(
        command,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - pid is ours, not model-supplied
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        return

    import os
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race on exit
        proc.kill()
