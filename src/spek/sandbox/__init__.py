"""Sandboxed execution backends.

The agent only ever talks to a `Sandbox` — never to `subprocess`, `open`, or
the host filesystem directly. This is the seam where we can swap Docker for
something else (firecracker, a remote runner, a mocked in-memory sandbox in
tests) without changing tool implementations or the agent loop.
"""

from spek.sandbox.base import WORK_DIR, RunResult, Sandbox, SandboxError
from spek.sandbox.docker import DockerSandbox, DockerUnavailableError

__all__ = [
    "DockerSandbox",
    "DockerUnavailableError",
    "RunResult",
    "Sandbox",
    "SandboxError",
    "WORK_DIR",
]
