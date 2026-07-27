"""Execution backends. The Docker client is faked — CI has no daemon."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from regin.sandbox.exec import DockerExecutor, LocalExecutor


class FakeExecResult:
    def __init__(self, exit_code: int, output: bytes) -> None:
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.removed = False

    def exec_run(self, cmd: list[str], workdir: str, demux: bool) -> FakeExecResult:
        self.commands.append(cmd[-1])
        return FakeExecResult(0, b"container output")

    def remove(self, force: bool) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self._container = container
        self.run_kwargs: dict[str, Any] = {}
        self.run_count = 0

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.run_count += 1
        self.run_kwargs = {"image": image, **kwargs}
        return self._container


class FakeDocker:
    def __init__(self) -> None:
        self.container = FakeContainer()
        self.containers = FakeContainers(self.container)


async def test_local_executor_runs_and_reports_exit_code(tmp_path: Path) -> None:
    result = await LocalExecutor(tmp_path).run(f'{sys.executable} -c "print(1+1)"', 30)
    assert result.exit_code == 0
    assert "2" in result.output
    assert result.timed_out is False


async def test_local_executor_flags_a_timeout(tmp_path: Path) -> None:
    result = await LocalExecutor(tmp_path).run(
        f'{sys.executable} -c "import time; time.sleep(30)"', 1
    )
    assert result.timed_out is True


async def test_docker_executor_runs_inside_the_container(tmp_path: Path) -> None:
    client = FakeDocker()
    executor = DockerExecutor("img:latest", tmp_path, client=client)

    result = await executor.run("pytest -q", 60)

    assert result.output == "container output"
    assert client.container.commands == ["pytest -q"]


async def test_docker_executor_bind_mounts_the_repository(tmp_path: Path) -> None:
    """The agent edits on the host; the container executes against the same bytes."""
    client = FakeDocker()
    await DockerExecutor("img:latest", tmp_path, client=client).run("ls", 60)

    volumes = client.containers.run_kwargs["volumes"]
    assert volumes[str(tmp_path.resolve())] == {"bind": "/workspace", "mode": "rw"}
    assert client.containers.run_kwargs["working_dir"] == "/workspace"


async def test_docker_executor_disables_networking(tmp_path: Path) -> None:
    """A solution must come from the repository, not from the internet."""
    client = FakeDocker()
    await DockerExecutor("img:latest", tmp_path, client=client).run("ls", 60)
    assert client.containers.run_kwargs["network_disabled"] is True


async def test_docker_executor_reuses_one_container_per_instance(tmp_path: Path) -> None:
    """A container per command would make every test run pay startup cost."""
    client = FakeDocker()
    executor = DockerExecutor("img:latest", tmp_path, client=client)

    await executor.run("echo a", 60)
    await executor.run("echo b", 60)

    assert client.containers.run_count == 1
    assert client.container.commands == ["echo a", "echo b"]


async def test_docker_executor_removes_the_container_on_close(tmp_path: Path) -> None:
    """A sweep would otherwise leak hundreds of containers."""
    client = FakeDocker()
    executor = DockerExecutor("img:latest", tmp_path, client=client)
    await executor.run("ls", 60)
    await executor.close()

    assert client.container.removed is True


async def test_closing_an_unstarted_executor_is_harmless(tmp_path: Path) -> None:
    """Instances that fail before their first command still run teardown."""
    await DockerExecutor("img:latest", tmp_path, client=FakeDocker()).close()
