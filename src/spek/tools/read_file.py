"""`read_file` tool."""

from __future__ import annotations

from typing import Any

from spek.sandbox.base import SandboxError
from spek.tools.base import Tool, ToolContext, ToolError

_SCHEMA: dict[str, Any] = {
    "name": "read_file",
    "description": (
        "Read a UTF-8 text file inside the project workdir. "
        "Files larger than 200 KB are truncated with a clear notice. "
        "Paths are relative to the workdir; absolute paths must start with /work."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside the workdir."},
        },
        "required": ["path"],
    },
}


def _run(input: dict[str, Any], ctx: ToolContext) -> str:
    path = input.get("path")
    if not isinstance(path, str) or not path:
        raise ToolError("read_file: 'path' is required and must be a non-empty string")
    try:
        return ctx.sandbox.read_file(path)
    except SandboxError as exc:
        raise ToolError(str(exc)) from exc


READ_FILE_TOOL = Tool(name="read_file", schema=_SCHEMA, run=_run)
