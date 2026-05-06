"""Docker-backed sandbox.

A single long-lived container is started for the duration of one
`spek build` run, with the host workdir bind-mounted at `/work`. Every
tool call shells out via `docker exec` so we pay the container start cost
only once.

We deliberately shell out to the `docker` CLI rather than depend on
`docker-py`: fewer moving parts, no daemon-version mismatches, and the
exact same behaviour developers see when they run `docker` themselves.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from spek.sandbox.base import WORK_DIR, RunResult, Sandbox, SandboxError


class DockerUnavailableError(SandboxError):
    """Raised when the Docker daemon is not reachable on this host."""


CACHE_DIR = "/spek-cache"
"""In-container path where pip/uv caches and user-installed binaries live.

Mounted as a named docker volume keyed by the host workdir, so successive
`spek build` runs against the same project skip the cost of reinstalling
`uv` and re-downloading wheels. The host workdir bind mount stays clean
because we route HOME there, not at `/work`.
"""


class DockerSandbox(Sandbox):
    """Long-lived `docker run -d` + per-command `docker exec`."""

    def __init__(
        self,
        host_workdir: str | Path,
        *,
        image: str,
        setup_cmd: list[str] | None = None,
        container_name: str | None = None,
        docker_cli: str | None = None,
        cache_volume: str | None = None,
    ) -> None:
        self._host_workdir = Path(host_workdir).resolve()
        self._host_workdir.mkdir(parents=True, exist_ok=True)
        self._image = image
        self._setup_cmd = setup_cmd
        # Stable name lets us reattach to a running container across spek
        # invocations on the same workdir, but we still recreate it on
        # every `__enter__` to avoid stale-state surprises.
        self._container_name = container_name or self._derive_name(self._host_workdir)
        # Cache volume is keyed by the workdir so unrelated projects don't
        # share a cache. Reused across runs of the same project.
        self._cache_volume = cache_volume or f"spek-cache-{self._derive_hash(self._host_workdir)}"
        self._docker = docker_cli or shutil.which("docker") or "docker"
        self._started = False

    @staticmethod
    def _derive_hash(host_workdir: Path) -> str:
        return hashlib.sha256(str(host_workdir).encode()).hexdigest()[:12]

    @classmethod
    def _derive_name(cls, host_workdir: Path) -> str:
        return f"spek-{cls._derive_hash(host_workdir)}"

    def __enter__(self) -> DockerSandbox:
        self._ensure_daemon()
        self._teardown_existing()
        self._start_container()
        if self._setup_cmd:
            res = self.run(self._setup_cmd, timeout=600)
            if res.exit_code != 0:
                raise SandboxError(
                    f"language setup_cmd failed (exit {res.exit_code}):\n{res.combined_output}"
                )
        self._started = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._teardown_existing()
        self._started = False

    def _ensure_daemon(self) -> None:
        try:
            res = subprocess.run(
                [self._docker, "info"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise DockerUnavailableError(
                "the `docker` CLI is not on PATH; install Docker Desktop or set DOCKER_CLI"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerUnavailableError("`docker info` timed out after 10s") from exc
        if res.returncode != 0:
            raise DockerUnavailableError(
                f"docker daemon not reachable (`docker info` exit {res.returncode}):\n"
                f"{res.stderr.strip() or res.stdout.strip()}"
            )

    def _teardown_existing(self) -> None:
        # `rm -f` is a no-op when the container does not exist.
        subprocess.run(
            [self._docker, "rm", "-f", self._container_name],
            check=False,
            capture_output=True,
        )

    def _start_container(self) -> None:
        uid = os.getuid()
        gid = os.getgid()
        # The container runs as root so the entrypoint can chown the cache
        # volume to the host UID. Every subsequent `docker exec` drops
        # privileges via `--user`, so tools and any code they run see the
        # host UID and files they create on the host bind-mount are owned
        # correctly.
        cmd = [
            self._docker,
            "run",
            "-d",
            "--name",
            self._container_name,
            "-v",
            f"{self._host_workdir}:{WORK_DIR}",
            "-v",
            f"{self._cache_volume}:{CACHE_DIR}",
            "-w",
            WORK_DIR,
            "-e",
            f"HOME={CACHE_DIR}",
            "-e",
            f"XDG_CACHE_HOME={CACHE_DIR}/.cache",
            "-e",
            f"UV_CACHE_DIR={CACHE_DIR}/.cache/uv",
            "-e",
            f"PATH={CACHE_DIR}/.local/bin:/usr/local/sbin:/usr/local/bin"
            ":/usr/sbin:/usr/bin:/sbin:/bin",
            "--entrypoint",
            "sleep",
            self._image,
            "infinity",
        ]
        res = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if res.returncode != 0:
            raise SandboxError(
                f"failed to start sandbox container ({self._image}):\n"
                f"{res.stderr.strip() or res.stdout.strip()}"
            )
        for _ in range(20):
            if self._is_running():
                break
            time.sleep(0.05)
        else:
            raise SandboxError("sandbox container did not reach running state in time")

        # Make the cache volume writable by the host user. The volume is
        # initialised root-owned by Docker the first time it's mounted, so
        # without this chown pip/uv can't write under HOME.
        chown = subprocess.run(
            [
                self._docker,
                "exec",
                self._container_name,
                "chown",
                "-R",
                f"{uid}:{gid}",
                CACHE_DIR,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if chown.returncode != 0:
            raise SandboxError(
                f"failed to prepare cache volume ({self._cache_volume}):\n"
                f"{chown.stderr.strip() or chown.stdout.strip()}"
            )

    def _is_running(self) -> bool:
        res = subprocess.run(
            [self._docker, "inspect", "-f", "{{.State.Running}}", self._container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"

    def run(self, argv: list[str], *, timeout: int = 120) -> RunResult:
        if not self._started:
            # Allows __enter__ to call run() for setup_cmd before flipping the flag.
            pass
        full = [
            self._docker,
            "exec",
            "--workdir",
            WORK_DIR,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            self._container_name,
            *argv,
        ]
        start = time.monotonic()
        try:
            res = subprocess.run(full, check=False, capture_output=True, text=True, timeout=timeout)
            elapsed = time.monotonic() - start
            return RunResult(
                exit_code=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
                duration_s=elapsed,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - start
            return RunResult(
                exit_code=124,
                stdout=exc.stdout.decode("utf-8", "replace") if exc.stdout else "",
                stderr=(
                    (exc.stderr.decode("utf-8", "replace") if exc.stderr else "")
                    + f"\nspek: command timed out after {timeout}s"
                ),
                duration_s=elapsed,
            )

    def write_file(self, path: str, content: str) -> int:
        rel = self._validate_path(path)
        target = posixpath.join(WORK_DIR, rel)
        parent = posixpath.dirname(target)
        # Two-step: ensure parent exists, then stream the body byte-for-byte
        # via stdin to `cat > target`. Avoids heredoc newline-fudging that
        # would mangle files whose content does (or does not) end with \n.
        user = f"{os.getuid()}:{os.getgid()}"
        if parent:
            mk = subprocess.run(
                [
                    self._docker,
                    "exec",
                    "--workdir",
                    WORK_DIR,
                    "--user",
                    user,
                    self._container_name,
                    "mkdir",
                    "-p",
                    parent,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if mk.returncode != 0:
                raise SandboxError(
                    f"write_file failed for {path!r} (mkdir): "
                    f"{mk.stderr.strip() or mk.stdout.strip()}"
                )
        full = [
            self._docker,
            "exec",
            "-i",
            "--workdir",
            WORK_DIR,
            "--user",
            user,
            self._container_name,
            "bash",
            "-c",
            f"cat > {shlex.quote(target)}",
        ]
        res = subprocess.run(
            full,
            input=content.encode("utf-8"),
            capture_output=True,
        )
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", "replace").strip()
            out = res.stdout.decode("utf-8", "replace").strip()
            raise SandboxError(f"write_file failed for {path!r}: {err or out}")
        return len(content.encode("utf-8"))

    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str:
        rel = self._validate_path(path)
        target = posixpath.join(WORK_DIR, rel)
        # `head -c N+1` lets us detect truncation by checking the byte count.
        full = [
            self._docker,
            "exec",
            "--workdir",
            WORK_DIR,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            self._container_name,
            "bash",
            "-lc",
            f"head -c {max_bytes + 1} {shlex.quote(target)}",
        ]
        res = subprocess.run(full, check=False, capture_output=True, text=True)
        if res.returncode != 0:
            raise SandboxError(
                f"read_file failed for {path!r}: {res.stderr.strip() or res.stdout.strip()}"
            )
        if len(res.stdout.encode("utf-8")) > max_bytes:
            return res.stdout[:max_bytes] + f"\n... [truncated; file exceeds {max_bytes} bytes]"
        return res.stdout

    def list_dir(self, path: str = ".") -> list[str]:
        rel = self._validate_path(path)
        target = posixpath.join(WORK_DIR, rel) if rel != "." else WORK_DIR
        res = subprocess.run(
            [
                self._docker,
                "exec",
                "--workdir",
                WORK_DIR,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                self._container_name,
                "bash",
                "-lc",
                f"ls -1A {shlex.quote(target)}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise SandboxError(
                f"list_dir failed for {path!r}: {res.stderr.strip() or res.stdout.strip()}"
            )
        return [ln for ln in res.stdout.splitlines() if ln]

    @staticmethod
    def _validate_path(path: str) -> str:
        """Normalise `path` and ensure it stays within `WORK_DIR`.

        Returns the path *relative* to `WORK_DIR` (suitable for joining with
        `posixpath.join(WORK_DIR, rel)`). Raises `SandboxError` for any
        path that resolves outside, including absolute paths to other
        locations, parent traversals, or empty strings.
        """
        if not path or path.strip() == "":
            raise SandboxError("path must be non-empty")
        # Treat /work and /work/<x> as legal absolute paths; everything else
        # absolute is rejected.
        if posixpath.isabs(path):
            if path == WORK_DIR:
                return "."
            if not path.startswith(WORK_DIR + "/"):
                raise SandboxError(f"absolute paths must be inside {WORK_DIR}: {path!r}")
            rel = path[len(WORK_DIR) + 1 :]
        else:
            rel = path
        normalised = posixpath.normpath(rel)
        if normalised.startswith("..") or normalised == "..":
            raise SandboxError(f"path escapes {WORK_DIR}: {path!r}")
        if normalised.startswith("/"):
            raise SandboxError(f"path resolved outside {WORK_DIR}: {path!r}")
        return normalised
