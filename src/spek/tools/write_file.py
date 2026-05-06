"""`write_file` tool.

Honours `ToolContext.plan_phase_write_only_path` so the LLM cannot escape
the phase-2 constraint of "only write `.spek/plan.md`" by passing a
different path.
"""

from __future__ import annotations

import posixpath
from typing import Any

from spek.sandbox.base import SandboxError
from spek.sandbox.docker import DockerSandbox
from spek.tools.base import Tool, ToolContext, ToolError

_SCHEMA: dict[str, Any] = {
    "name": "write_file",
    "description": (
        "Create or overwrite a UTF-8 text file inside the project workdir. "
        "Parent directories are created automatically. Returns the number of "
        "bytes written."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workdir."},
            "content": {"type": "string", "description": "Full file contents to write."},
        },
        "required": ["path", "content"],
    },
}


def _normalise(path: str) -> str:
    """Normalise a path the same way the sandbox would, for path-equality checks."""
    return DockerSandbox._validate_path(path)


def _run(input: dict[str, Any], ctx: ToolContext) -> str:
    path = input.get("path")
    content = input.get("content")
    if not isinstance(path, str) or not path:
        raise ToolError("write_file: 'path' is required and must be a non-empty string")
    if not isinstance(content, str):
        raise ToolError("write_file: 'content' is required and must be a string")

    if ctx.plan_phase_write_only_path is not None:
        try:
            normalised_target = _normalise(path)
        except SandboxError as exc:
            raise ToolError(str(exc)) from exc
        allowed_normalised = _normalise(ctx.plan_phase_write_only_path)
        if normalised_target != allowed_normalised:
            raise ToolError(
                "during the plan phase, write_file may only write to "
                f"{ctx.plan_phase_write_only_path!r}; got {path!r}"
            )

    try:
        n = ctx.sandbox.write_file(path, content)
    except SandboxError as exc:
        raise ToolError(str(exc)) from exc
    return f"wrote {n} bytes to {posixpath.normpath(path)}"


WRITE_FILE_TOOL = Tool(name="write_file", schema=_SCHEMA, run=_run)
