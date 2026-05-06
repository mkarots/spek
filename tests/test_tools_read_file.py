"""Tests for the read_file tool."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from spek.journal import Journal
from spek.tools import ALL_TOOLS, PHASE_CLARIFY_TOOLS, ToolContext, dispatch
from tests.fakes import FakeSandbox


def _ctx(tmp_path: Path, sandbox: FakeSandbox) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        journal=Journal(tmp_path / "journal.jsonl"),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )


def test_read_file_returns_contents(tmp_path: Path) -> None:
    box = FakeSandbox(files={"a.py": "print(1)\n"})
    out, is_err = dispatch(
        ALL_TOOLS, "read_file", {"path": "a.py"}, _ctx(tmp_path, box), allowed=PHASE_CLARIFY_TOOLS
    )
    assert is_err is False
    assert out == "print(1)\n"


def test_read_file_reports_missing_file_as_tool_error(tmp_path: Path) -> None:
    box = FakeSandbox()
    out, is_err = dispatch(
        ALL_TOOLS,
        "read_file",
        {"path": "nope.py"},
        _ctx(tmp_path, box),
        allowed=PHASE_CLARIFY_TOOLS,
    )
    assert is_err is True
    assert "does not exist" in out


def test_read_file_rejects_path_traversal(tmp_path: Path) -> None:
    box = FakeSandbox()
    out, is_err = dispatch(
        ALL_TOOLS,
        "read_file",
        {"path": "../etc/passwd"},
        _ctx(tmp_path, box),
        allowed=PHASE_CLARIFY_TOOLS,
    )
    assert is_err is True
    assert "escapes" in out or "outside" in out


def test_read_file_rejects_missing_path(tmp_path: Path) -> None:
    box = FakeSandbox()
    out, is_err = dispatch(
        ALL_TOOLS, "read_file", {}, _ctx(tmp_path, box), allowed=PHASE_CLARIFY_TOOLS
    )
    assert is_err is True
    assert "'path' is required" in out


@pytest.mark.parametrize("disallowed_phase", ["bash"])
def test_read_file_works_in_clarify_phase(tmp_path: Path, disallowed_phase: str) -> None:
    box = FakeSandbox(files={"x.py": "x"})
    # Sanity check: read_file is in clarify whitelist; bash isn't.
    assert "read_file" in PHASE_CLARIFY_TOOLS
    assert disallowed_phase not in PHASE_CLARIFY_TOOLS
    _, is_err = dispatch(
        ALL_TOOLS, "read_file", {"path": "x.py"}, _ctx(tmp_path, box), allowed=PHASE_CLARIFY_TOOLS
    )
    assert is_err is False


def test_phase_whitelist_blocks_disallowed_tool(tmp_path: Path) -> None:
    box = FakeSandbox()
    out, is_err = dispatch(
        ALL_TOOLS,
        "bash",
        {"command": "echo x"},
        _ctx(tmp_path, box),
        allowed=PHASE_CLARIFY_TOOLS,
    )
    assert is_err is True
    assert "not available in the current phase" in out
