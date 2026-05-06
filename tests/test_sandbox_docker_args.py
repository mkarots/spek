"""Unit-level tests for the docker-cli argv construction.

These do not require a Docker daemon. We monkeypatch `subprocess.run` and
assert that the sandbox builds the right command line for `docker run`,
`docker exec`, and the volume/HOME setup. Catches the kind of regression
that broke the v0 sandbox: missing HOME/cache-volume meant `pip install`
tried to write to `/` and failed with EACCES.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from spek.sandbox.base import RunResult, SandboxError
from spek.sandbox.docker import CACHE_DIR, DockerSandbox


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every `subprocess.run` argv and return canned successful results."""
    calls: list[list[str]] = []

    class _Result:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "true"  # for `docker inspect ... .State.Running`
            self.stderr = ""

    def fake_run(argv: list[str], **_kwargs: Any) -> _Result:
        calls.append(list(argv))
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_start_container_mounts_workdir_and_cache_volume(
    fake_subprocess: list[list[str]], tmp_path: Path
) -> None:
    box = DockerSandbox(tmp_path / "work", image="python:3.12-slim", setup_cmd=None)
    box.__enter__()

    run_calls = [c for c in fake_subprocess if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 1, "expected exactly one `docker run` invocation"

    argv = run_calls[0]
    workdir = str((tmp_path / "work").resolve())

    # Bind mount for the user's workdir
    assert "-v" in argv and f"{workdir}:/work" in argv
    # Named cache volume for HOME / pip / uv caches
    cache_mount = next((a for a in argv if a.endswith(f":{CACHE_DIR}")), None)
    assert cache_mount is not None
    assert cache_mount.startswith("spek-cache-")

    # HOME and cache-related env vars must be set so pip/uv don't try to
    # write to `/` (the bug that prompted this whole test file).
    env_args = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-e"]
    assert f"HOME={CACHE_DIR}" in env_args
    assert any(e.startswith("XDG_CACHE_HOME=") for e in env_args)
    assert any(e.startswith("UV_CACHE_DIR=") for e in env_args)
    path_env = next((e for e in env_args if e.startswith("PATH=")), None)
    assert path_env is not None
    assert f"{CACHE_DIR}/.local/bin" in path_env

    # Container itself must run as root so the chown step works; per-exec
    # `--user` is what drops privileges for tool calls.
    assert "--user" not in argv[: argv.index("run") + 5]


def test_start_container_chowns_cache_volume(
    fake_subprocess: list[list[str]], tmp_path: Path
) -> None:
    box = DockerSandbox(tmp_path / "work", image="python:3.12-slim", setup_cmd=None)
    box.__enter__()

    chown_calls = [c for c in fake_subprocess if len(c) >= 4 and c[1] == "exec" and "chown" in c]
    assert chown_calls, "must chown the cache volume to host UID after container start"
    assert CACHE_DIR in chown_calls[0]


def test_run_uses_per_exec_user_to_drop_privileges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[list[str]] = []

    class _R:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(argv: list[str], **_kwargs: Any) -> _R:
        captured.append(list(argv))
        if len(argv) >= 2 and argv[1] == "inspect":
            return _R(stdout="true")
        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    box = DockerSandbox(tmp_path / "work", image="python:3.12-slim", setup_cmd=None)
    box.__enter__()
    captured.clear()

    box.run(["echo", "hi"])

    exec_calls = [c for c in captured if len(c) >= 2 and c[1] == "exec"]
    assert exec_calls, "expected docker exec to run the command"
    argv = exec_calls[0]
    assert "--user" in argv
    user_idx = argv.index("--user")
    assert ":" in argv[user_idx + 1], "user must be uid:gid"


def test_setup_cmd_failure_is_surfaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Non-zero exit from setup_cmd raises SandboxError with combined output."""

    class _R:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    state = {"setup_done": False}

    def fake_run(argv: list[str], **_kwargs: Any) -> _R:
        if argv[1] == "info":
            return _R(returncode=0)
        if argv[1] == "rm":
            return _R(returncode=0)
        if argv[1] == "run":
            return _R(returncode=0)
        if argv[1] == "inspect":
            return _R(returncode=0, stdout="true")
        if argv[1] == "exec" and "chown" in argv:
            return _R(returncode=0)
        # The first non-chown exec is our setup_cmd; fail it.
        if not state["setup_done"]:
            state["setup_done"] = True
            return _R(returncode=42, stderr="setup boom")
        return _R(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    setup = ["bash", "-lc", "false"]
    box = DockerSandbox(tmp_path / "work", image="python:3.12-slim", setup_cmd=setup)
    with pytest.raises(SandboxError, match="setup_cmd failed"):
        box.__enter__()


def test_run_returns_result_with_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Smoke test that `run()` packages docker exec output into a RunResult."""

    class _R:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv: list[str], **_kwargs: Any) -> _R:
        if len(argv) >= 2 and argv[1] == "inspect":
            return _R(stdout="true")
        if len(argv) >= 2 and argv[1] == "exec" and "echo" in argv:
            return _R(stdout="hi\n", returncode=0)
        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)

    box = DockerSandbox(tmp_path / "work", image="python:3.12-slim", setup_cmd=None)
    box.__enter__()

    res = box.run(["echo", "hi"])
    assert isinstance(res, RunResult)
    assert res.exit_code == 0
    assert res.stdout == "hi\n"
