"""Command execution backends.

`bash` is the only tool that needs a backend. The filesystem tools keep operating on the
host path, which the Docker backend bind-mounts into the container — so the agent edits
files on the host and executes tests inside the container, against the same bytes.

That split is deliberate: test execution is the part that runs arbitrary repository code,
so it is the part worth isolating. Proxying every read and write through `docker exec`
would buy little and would mean the benchmarked agent no longer matches the shipped one.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False


class Executor(Protocol):
    async def run(self, command: str, timeout_s: int) -> ExecResult: ...

    async def close(self) -> None: ...


class LocalExecutor:
    """Runs on the host, in its own process group so a timeout reclaims children."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def run(self, command: str, timeout_s: int) -> ExecResult:
        proc = await _spawn(command, self._root)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return ExecResult(exit_code=-1, output="", timed_out=True)
        return ExecResult(proc.returncode or 0, stdout.decode(errors="replace"))

    async def close(self) -> None:
        return None


class DockerExecutor:
    """Runs inside a container with the repository bind-mounted at `workdir`.

    One container per task instance, matching the official SWE-bench harness. The `docker`
    SDK is imported lazily so that neither the CLI nor the unit tests require Docker.
    """

    def __init__(
        self,
        image: str,
        host_dir: Path,
        workdir: str = "/workspace",
        client: Any | None = None,
    ) -> None:
        self._image = image
        self._host_dir = host_dir.resolve()
        self._workdir = workdir
        self._client = client
        self._container: Any | None = None

    def _ensure_container(self) -> Any:
        if self._container is not None:
            return self._container

        client = self._client
        if client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "the docker SDK is not installed; install with `pip install regin[bench]`"
                ) from exc
            client = docker.from_env()
            self._client = client

        self._container = client.containers.run(
            self._image,
            command="sleep infinity",
            detach=True,
            working_dir=self._workdir,
            volumes={str(self._host_dir): {"bind": self._workdir, "mode": "rw"}},
            network_disabled=True,  # a solution must come from the repo, not the internet
            mem_limit="4g",
            auto_remove=False,
        )
        return self._container

    async def run(self, command: str, timeout_s: int) -> ExecResult:
        container = await asyncio.to_thread(self._ensure_container)

        def _exec() -> tuple[int, bytes]:
            result = container.exec_run(
                cmd=["/bin/sh", "-c", command],
                workdir=self._workdir,
                demux=False,
            )
            return result.exit_code or 0, result.output or b""

        try:
            exit_code, output = await asyncio.wait_for(asyncio.to_thread(_exec), timeout_s)
        except TimeoutError:
            return ExecResult(exit_code=-1, output="", timed_out=True)

        return ExecResult(exit_code, output.decode(errors="replace"))

    async def close(self) -> None:
        """Containers are never left running — a sweep would otherwise leak hundreds."""
        if self._container is None:
            return
        container = self._container
        self._container = None
        await asyncio.to_thread(_force_remove, container)


def _force_remove(container: Any) -> None:
    # Teardown must never fail a run — a leaked container is a smaller problem than a
    # sweep that dies on cleanup after the expensive part already succeeded.
    with contextlib.suppress(Exception):  # pragma: no cover
        container.remove(force=True)


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
        subprocess.run(
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
