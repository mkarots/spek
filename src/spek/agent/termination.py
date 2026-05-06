"""Termination check for the execute phase.

We are "done" when, within the recent journal history:
- The configured build command exited 0; AND
- The configured test command exited 0 with at least one test collected.

We don't try to infer this from semantic LLM output — only from the literal
tool_result entries. That keeps the check deterministic and trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spek.config import CommandConfig
from spek.journal import Journal
from spek.languages.base import LanguageProfile

_LOOKBACK_TOOL_RESULTS = 20  # how many recent tool_results to consider


@dataclass(frozen=True)
class TerminationStatus:
    done: bool
    reason: str
    test_count: int = 0


def _extract_text(content: Any) -> str:
    """Pull plain text out of a tool_result content block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if "content" in block and isinstance(block["content"], str):
                    out.append(block["content"])
                elif "content" in block and isinstance(block["content"], list):
                    out.append(_extract_text(block["content"]))
                elif block.get("type") == "text" and isinstance(block.get("text"), str):
                    out.append(block["text"])
        return "\n".join(out)
    return ""


_BASH_HEADER_RE = re.compile(r"exit_code=(?P<exit>-?\d+)\b")


def _looks_like_bash_result(text: str) -> bool:
    return _BASH_HEADER_RE.match(text) is not None


def _bash_exit_code(text: str) -> int | None:
    m = _BASH_HEADER_RE.match(text)
    if not m:
        return None
    try:
        return int(m.group("exit"))
    except ValueError:  # pragma: no cover
        return None


def check(
    journal: Journal,
    cfg: CommandConfig,
    profile: LanguageProfile,
) -> TerminationStatus:
    """Inspect the journal and decide whether the run can stop."""
    entries = journal.read_all()
    # Walk the most recent entries; pair each tool_result with the prior
    # assistant tool_use to recover what command was run.
    pairs: list[tuple[str, str]] = []  # (command, tool_result_text)
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if e.kind != "tool_result":
            continue
        text = _extract_text(e.content)
        if not _looks_like_bash_result(text):
            continue
        # Find the most recent assistant turn before this tool_result and
        # extract the command from the corresponding tool_use block.
        cmd: str | None = None
        for j in range(i - 1, -1, -1):
            ej = entries[j]
            if ej.kind != "assistant":
                continue
            for block in ej.content if isinstance(ej.content, list) else []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "bash"
                ):
                    inp = block.get("input") or {}
                    if isinstance(inp, dict):
                        c = inp.get("command")
                        if isinstance(c, str):
                            cmd = c
            break
        if cmd is None:
            continue
        pairs.append((cmd, text))
        if len(pairs) >= _LOOKBACK_TOOL_RESULTS:
            break

    # Walk most-recent-first to find the latest test/build invocations.
    last_test: tuple[str, str] | None = None
    last_build: tuple[str, str] | None = None
    for cmd, text in pairs:
        if last_test is None and cmd.strip() == cfg.test_command.strip():
            last_test = (cmd, text)
        if last_build is None and cmd.strip() == cfg.build_command.strip():
            last_build = (cmd, text)
        if last_test and last_build:
            break

    if last_build is None:
        return TerminationStatus(False, "no recent build command run")
    if _bash_exit_code(last_build[1]) != 0:
        return TerminationStatus(False, "most recent build did not exit 0")
    if last_test is None:
        return TerminationStatus(False, "no recent test command run")
    if _bash_exit_code(last_test[1]) != 0:
        return TerminationStatus(False, "most recent test run did not exit 0")
    test_count = profile.parse_test_count(last_test[1])
    if test_count <= 0:
        return TerminationStatus(False, "test run collected zero tests", test_count=0)
    return TerminationStatus(True, "build+test green", test_count=test_count)
