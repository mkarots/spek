"""`grep` tool: thin wrapper around `grep -rn` inside the sandbox."""

from __future__ import annotations

from typing import Any

from spek.tools.base import Tool, ToolContext, ToolError

_MAX_OUTPUT = 32_000  # bytes; bigger than this is rarely useful to the LLM

_SCHEMA: dict[str, Any] = {
    "name": "grep",
    "description": (
        "Recursively search files in the workdir for a fixed string or regex "
        "pattern. Returns lines in `path:line:match` form, capped to 32 KB."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Pattern to search for."},
            "path": {
                "type": "string",
                "description": "Optional sub-path to limit the search (default: '.').",
            },
            "fixed_string": {
                "type": "boolean",
                "description": "If true, treat pattern as a fixed string (grep -F).",
            },
        },
        "required": ["pattern"],
    },
}


def _run(input: dict[str, Any], ctx: ToolContext) -> str:
    pattern = input.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("grep: 'pattern' is required and must be a non-empty string")
    path = input.get("path") or "."
    fixed = bool(input.get("fixed_string", False))

    argv = ["grep", "-rn"]
    if fixed:
        argv.append("-F")
    argv.append("--")
    argv.append(pattern)
    argv.append(path)

    res = ctx.sandbox.run(argv, timeout=30)
    # grep exit codes: 0 = match, 1 = no match, >1 = error.
    if res.exit_code == 1:
        return "no matches"
    if res.exit_code > 1:
        # Surface as an error so the model sees it, but don't raise — bad
        # patterns happen and the LLM should iterate.
        return f"grep error (exit {res.exit_code}):\n{res.combined_output}".strip()

    out = res.stdout
    if len(out.encode("utf-8")) > _MAX_OUTPUT:
        return out[:_MAX_OUTPUT] + f"\n... [truncated; output exceeds {_MAX_OUTPUT} bytes]"
    return out or "no matches"


GREP_TOOL = Tool(name="grep", schema=_SCHEMA, run=_run)
