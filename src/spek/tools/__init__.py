"""Anthropic native tool-use tools for the spek agent.

Every tool exposes a `SCHEMA` (the Anthropic tool descriptor) and a `run`
function with the signature `run(input: dict, ctx: ToolContext) -> str`.
The agent loop wires these together via `dispatch()`, after applying the
phase-specific tool whitelist.

The list of tools is intentionally tiny: read/write/search/exec/ask. Per
the plan, that is enough surface area to build, debug, and ship a software
package.
"""

from __future__ import annotations

from spek.tools.base import (
    PHASE_CLARIFY_TOOLS,
    PHASE_EXECUTE_TOOLS,
    PHASE_PLAN_TOOLS,
    Tool,
    ToolContext,
    ToolError,
    ToolNotAllowed,
    dispatch,
)
from spek.tools.bash import BASH_TOOL
from spek.tools.epistemic import EPISTEMIC_TOOL
from spek.tools.grep import GREP_TOOL
from spek.tools.read_file import READ_FILE_TOOL
from spek.tools.write_file import WRITE_FILE_TOOL

ALL_TOOLS: list[Tool] = [
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    GREP_TOOL,
    BASH_TOOL,
    EPISTEMIC_TOOL,
]

ANTHROPIC_TOOL_SCHEMAS = [t.schema for t in ALL_TOOLS]


__all__ = [
    "ALL_TOOLS",
    "ANTHROPIC_TOOL_SCHEMAS",
    "BASH_TOOL",
    "EPISTEMIC_TOOL",
    "GREP_TOOL",
    "PHASE_CLARIFY_TOOLS",
    "PHASE_EXECUTE_TOOLS",
    "PHASE_PLAN_TOOLS",
    "READ_FILE_TOOL",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolNotAllowed",
    "WRITE_FILE_TOOL",
    "dispatch",
]
