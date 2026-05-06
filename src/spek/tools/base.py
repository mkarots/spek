"""Tool plumbing: `ToolContext`, `Tool`, dispatcher, phase whitelists."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from spek.journal import Journal
from spek.sandbox.base import Sandbox


class ToolError(RuntimeError):
    """Raised by tools to surface a recoverable error back to the model.

    The dispatcher converts this into a `tool_result` with `is_error=True`
    so the LLM sees the failure and can react. Unhandled exceptions in tool
    code are also turned into error results so the loop never crashes.
    """


class ToolNotAllowed(ToolError):
    """The tool exists but is not in the current phase's whitelist."""


@dataclass
class ToolContext:
    """Per-call dependencies handed to every tool.

    Attributes
    ----------
    sandbox:
        Where filesystem and process operations happen.
    journal:
        Append-only log; tools are journaled by the dispatcher, not by
        themselves, but the context is here for tools that need to read
        history (e.g. termination has to find the last build/test runs).
    stdin / stdout:
        Streams used by the `epistemic` tool for human handoff. Indirected
        so tests can substitute `io.StringIO`.
    plan_phase_write_only_path:
        When set, `write_file` rejects any path that does not equal this
        value. Used to keep the LLM honest during phase 2 (Plan).
    """

    sandbox: Sandbox
    journal: Journal
    stdin: Any
    stdout: Any
    plan_phase_write_only_path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class _ToolFn(Protocol):
    def __call__(self, input: dict[str, Any], ctx: ToolContext) -> str: ...


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict[str, Any]
    run: _ToolFn


# Per-phase whitelists. Names match `Tool.name`; kept here so the
# dispatcher can reject out-of-phase calls without each tool having to know
# about phases.
PHASE_CLARIFY_TOOLS: frozenset[str] = frozenset({"read_file", "grep", "epistemic"})
PHASE_PLAN_TOOLS: frozenset[str] = frozenset({"read_file", "grep", "write_file"})
PHASE_EXECUTE_TOOLS: frozenset[str] = frozenset(
    {"read_file", "write_file", "grep", "bash", "epistemic"}
)


def dispatch(
    tools: list[Tool],
    name: str,
    input: dict[str, Any],
    ctx: ToolContext,
    *,
    allowed: frozenset[str],
) -> tuple[str, bool]:
    """Run a tool by `name`. Returns `(result_text, is_error)`.

    Errors are returned as `(message, True)` rather than raised — the agent
    loop wraps them in a `tool_result` block with `is_error=True` so the
    LLM can recover. We only ever raise for genuinely unrecoverable
    programming errors (the calling code shouldn't catch).
    """
    if name not in allowed:
        return (
            f"tool {name!r} is not available in the current phase "
            f"(allowed: {', '.join(sorted(allowed))})",
            True,
        )
    matched = next((t for t in tools if t.name == name), None)
    if matched is None:
        return (f"unknown tool: {name!r}", True)
    try:
        return matched.run(input, ctx), False
    except ToolError as exc:
        return (str(exc), True)
    except Exception as exc:  # pragma: no cover - defensive
        return (f"{type(exc).__name__}: {exc}", True)
