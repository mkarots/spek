"""Integration tests for the Docker sandbox.

Skipped automatically when `docker info` fails so a Docker-less environment
stays green. Runs the full lifecycle end-to-end against a real container.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from spek.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    res = subprocess.run([docker, "info"], capture_output=True)
    return res.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _docker_available(), reason="docker daemon not reachable"),
]


def test_docker_sandbox_round_trip(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    with DockerSandbox(
        workdir,
        image="python:3.12-slim",
        setup_cmd=None,
    ) as box:
        n = box.write_file("hello.txt", "world\n")
        assert n == len("world\n")
        assert box.read_file("hello.txt") == "world\n"
        assert "hello.txt" in box.list_dir()
        res = box.run(["python", "-c", "print(1+1)"], timeout=30)
        assert res.exit_code == 0
        assert "2" in res.stdout

    # File survives container teardown because the workdir is bind-mounted.
    assert (workdir / "hello.txt").read_text() == "world\n"
