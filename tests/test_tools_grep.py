"""Tests for the grep tool."""

from __future__ import annotations

import io
from pathlib import Path

from spek.journal import Journal
from spek.sandbox.base import RunResult
from spek.tools import ALL_TOOLS, PHASE_EXECUTE_TOOLS, ToolContext, dispatch
from tests.fakes import FakeSandbox


def _ctx(tmp_path: Path, box: FakeSandbox) -> ToolContext:
    return ToolContext(
        sandbox=box,
        journal=Journal(tmp_path / "journal.jsonl"),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )


def _grep_handler(stdout: str, exit_code: int) -> tuple:
    def predicate(argv: list[str]) -> bool:
        return argv[0] == "grep"

    def handler(_argv: list[str]) -> RunResult:
        return RunResult(exit_code=exit_code, stdout=stdout, stderr="", duration_s=0.0)

    return predicate, handler


def test_grep_returns_matches(tmp_path: Path) -> None:
    box = FakeSandbox()
    box.register(*_grep_handler("a.py:1:foo\nb.py:2:foo\n", 0))

    out, is_err = dispatch(
        ALL_TOOLS, "grep", {"pattern": "foo"}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )

    assert is_err is False
    assert "a.py:1:foo" in out


def test_grep_no_matches_returns_message(tmp_path: Path) -> None:
    box = FakeSandbox()
    box.register(*_grep_handler("", 1))

    out, _ = dispatch(
        ALL_TOOLS, "grep", {"pattern": "nope"}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )

    assert out == "no matches"


def test_grep_error_exit_code_surfaces_to_model(tmp_path: Path) -> None:
    box = FakeSandbox()
    box.register(*_grep_handler("grep: invalid pattern\n", 2))

    out, is_err = dispatch(
        ALL_TOOLS, "grep", {"pattern": "[bad"}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )

    # We do NOT raise on grep errors; the model should iterate.
    assert is_err is False
    assert "grep error" in out


def test_grep_truncates_huge_output(tmp_path: Path) -> None:
    box = FakeSandbox()
    huge = "a" * 100_000
    box.register(*_grep_handler(huge, 0))

    out, _ = dispatch(
        ALL_TOOLS, "grep", {"pattern": "x"}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )

    assert "[truncated" in out


def test_grep_rejects_empty_pattern(tmp_path: Path) -> None:
    box = FakeSandbox()
    _, is_err = dispatch(
        ALL_TOOLS, "grep", {"pattern": ""}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )
    assert is_err is True
