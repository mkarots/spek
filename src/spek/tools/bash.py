"""`bash` tool: run a shell command inside the sandbox."""

from __future__ import annotations

from typing import Any

from spek.tools.base import Tool, ToolContext, ToolError

_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 600  # cap so the model can't quietly stall the agent for hours
_MAX_OUTPUT = 64_000

_SCHEMA: dict[str, Any] = {
    "name": "bash",
    "description": (
        "Execute a shell command inside the project workdir (Docker sandbox). "
        "Returns combined stdout/stderr and the exit code. Use this for build, "
        "test, lint, and ad-hoc inspection. Default timeout 120s, max 600s."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120, max 600).",
            },
        },
        "required": ["command"],
    },
}


def _run(input: dict[str, Any], ctx: ToolContext) -> str:
    cmd = input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ToolError("bash: 'command' is required and must be a non-empty string")
    timeout = input.get("timeout", _DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = _DEFAULT_TIMEOUT
    timeout = min(timeout, _MAX_TIMEOUT)

    res = ctx.sandbox.run(["bash", "-lc", cmd], timeout=timeout)
    body = res.combined_output
    if len(body.encode("utf-8")) > _MAX_OUTPUT:
        body = body[:_MAX_OUTPUT] + f"\n... [truncated; output exceeds {_MAX_OUTPUT} bytes]"
    return f"exit_code={res.exit_code} duration={res.duration_s:.2f}s\n{body}"


BASH_TOOL = Tool(name="bash", schema=_SCHEMA, run=_run)
