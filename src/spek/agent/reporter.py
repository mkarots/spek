"""Lightweight progress reporting for the agent loop.

The loop is otherwise silent by design — every meaningful event lands in
`.spek/journal.jsonl`, which is the source of truth. But for a human
running `spek build` in a terminal, "nothing prints for 30s while the
LLM thinks" is bad UX. This module adds a thin observer that the loop
calls at phase transitions, LLM turns, tool calls, and tool results.

Design:
- `Reporter` is a small Protocol with one method per event. Two impls:
  `ConsoleReporter` (writes one short line per event) and `NullReporter`
  (silent, used by tests by default).
- The loop owns *what* events fire; the reporter owns *how* they look.
  This keeps the loop's logic uncluttered (SRP) and lets us swap in a
  JSON reporter, a TUI reporter, etc., later.
- Reporters never raise — a print() failing must not crash the agent.
"""

from __future__ import annotations

import io
import json
import sys
import time
from typing import Any, Protocol


class Reporter(Protocol):
    """Observer interface used by the agent loop."""

    def start(self, *, spec_name: str, workdir: str, model: str) -> None: ...

    def phase(self, name: str) -> None: ...

    def llm_turn(self, *, phase: str, turn: int) -> None: ...

    def tool_call(self, *, name: str, summary: str) -> None: ...

    def tool_result(self, *, name: str, ok: bool, summary: str) -> None: ...

    def nudge(self, *, phase: str, reason: str) -> None: ...

    def phase_done(self, *, phase: str, turns: int) -> None: ...

    def finished(self, *, success: bool, reason: str, test_count: int) -> None: ...


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class NullReporter:
    """No-op reporter. The default for tests."""

    def start(self, *, spec_name: str, workdir: str, model: str) -> None: ...
    def phase(self, name: str) -> None: ...
    def llm_turn(self, *, phase: str, turn: int) -> None: ...
    def tool_call(self, *, name: str, summary: str) -> None: ...
    def tool_result(self, *, name: str, ok: bool, summary: str) -> None: ...
    def nudge(self, *, phase: str, reason: str) -> None: ...
    def phase_done(self, *, phase: str, turns: int) -> None: ...
    def finished(self, *, success: bool, reason: str, test_count: int) -> None: ...


_MAX_SUMMARY = 100


def _truncate(text: str, limit: int = _MAX_SUMMARY) -> str:
    """Return a single-line, length-capped version of ``text``."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "\u2026"


class ConsoleReporter:
    """Prints a short, prefix-tagged line per event to a stream (default: stdout).

    Output is unstructured but stable; users grep for prefixes if they
    want to script around it. Each event is one line so it composes well
    with `tee` and `less`.
    """

    PREFIX = {
        "start": "spek:",
        "phase": "==>",
        "turn": "  ..",
        "tool": "   *",
        "result_ok": "   =",
        "result_err": "   !",
        "nudge": "   ?",
        "done": "<==",
        "final_ok": "spek: \u2713",
        "final_fail": "spek: \u2717",
    }

    def __init__(self, stream: io.TextIOBase | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._t0 = time.monotonic()

    def _line(self, prefix: str, text: str) -> None:
        elapsed = int(time.monotonic() - self._t0)
        try:
            self._stream.write(f"[{elapsed:>4}s] {prefix} {text}\n")
            self._stream.flush()
        except Exception:
            # Never let progress printing break the run.
            pass

    def start(self, *, spec_name: str, workdir: str, model: str) -> None:
        self._line(
            self.PREFIX["start"],
            f"build start \u2014 spec={spec_name} workdir={workdir} model={model}",
        )

    def phase(self, name: str) -> None:
        self._line(self.PREFIX["phase"], f"phase {name}")

    def llm_turn(self, *, phase: str, turn: int) -> None:
        self._line(self.PREFIX["turn"], f"[{phase}] LLM turn {turn}")

    def tool_call(self, *, name: str, summary: str) -> None:
        self._line(self.PREFIX["tool"], f"tool {name}({_truncate(summary)})")

    def tool_result(self, *, name: str, ok: bool, summary: str) -> None:
        prefix = self.PREFIX["result_ok"] if ok else self.PREFIX["result_err"]
        tag = "ok" if ok else "ERR"
        self._line(prefix, f"{name} {tag}: {_truncate(summary)}")

    def nudge(self, *, phase: str, reason: str) -> None:
        self._line(self.PREFIX["nudge"], f"[{phase}] nudge: {_truncate(reason)}")

    def phase_done(self, *, phase: str, turns: int) -> None:
        self._line(self.PREFIX["done"], f"phase {phase} done ({turns} turns)")

    def finished(self, *, success: bool, reason: str, test_count: int) -> None:
        prefix = self.PREFIX["final_ok"] if success else self.PREFIX["final_fail"]
        if success:
            self._line(prefix, f"{reason} (tests passed: {test_count})")
        else:
            self._line(prefix, reason)


# ---------------------------------------------------------------------------
# Tool-input summarisers
# ---------------------------------------------------------------------------


def summarise_tool_input(name: str, args: dict[str, Any]) -> str:
    """Best-effort one-line summary of a tool_use input for progress output.

    We special-case the five built-in tools so users see the meaningful
    field (e.g. the bash command, the file being written) rather than a
    JSON blob. Unknown tools fall back to a compact JSON dump.
    """
    if name == "bash":
        return str(args.get("command", ""))
    if name == "read_file":
        return str(args.get("path", ""))
    if name == "write_file":
        path = args.get("path", "")
        body = args.get("content", "")
        return f"{path} ({len(body)} bytes)"
    if name == "grep":
        pattern = args.get("pattern", "")
        path = args.get("path", "")
        return f"{pattern!r} in {path or '.'}"
    if name == "epistemic":
        return str(args.get("question", ""))
    try:
        return json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return str(args)


def summarise_tool_result(text: str) -> str:
    """Single-line summary of a tool_result payload for progress output."""
    if not text:
        return "<empty>"
    return text.splitlines()[0] if "\n" in text else text
