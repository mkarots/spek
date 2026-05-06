"""Sandbox interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# All sandboxes mount the host workdir at this path inside the container.
# Tools refer to paths relative to (or rooted at) this directory; absolute
# paths outside it are rejected by the path-safety guard.
WORK_DIR = "/work"


class SandboxError(RuntimeError):
    """Base class for sandbox failures the agent loop should surface."""


@dataclass(frozen=True)
class RunResult:
    """The outcome of a `Sandbox.run` invocation."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def combined_output(self) -> str:
        if not self.stderr:
            return self.stdout
        if not self.stdout:
            return self.stderr
        return f"{self.stdout}\n--- stderr ---\n{self.stderr}"


class Sandbox(ABC):
    """Execute commands and read/write files inside an isolated environment."""

    @abstractmethod
    def run(self, argv: list[str], *, timeout: int = 120) -> RunResult:
        """Execute `argv` in the sandbox at `WORK_DIR`."""

    @abstractmethod
    def write_file(self, path: str, content: str) -> int:
        """Write `content` to `path` inside the sandbox; returns bytes written.

        `path` must resolve inside `WORK_DIR`. Implementations must reject
        any path that traverses outside (`..`, absolute paths, symlinks).
        """

    @abstractmethod
    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str:
        """Read `path` inside the sandbox; truncate to `max_bytes` with a notice."""

    @abstractmethod
    def list_dir(self, path: str = ".") -> list[str]:
        """List entries in `path` (relative to `WORK_DIR`)."""

    @abstractmethod
    def __enter__(self) -> Sandbox: ...

    @abstractmethod
    def __exit__(self, exc_type, exc, tb) -> None: ...
