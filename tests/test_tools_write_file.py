"""Tests for the write_file tool, including phase-2 path restriction."""

from __future__ import annotations

import io
from pathlib import Path

from spek.journal import Journal
from spek.tools import (
    ALL_TOOLS,
    PHASE_EXECUTE_TOOLS,
    PHASE_PLAN_TOOLS,
    ToolContext,
    dispatch,
)
from tests.fakes import FakeSandbox


def _ctx(tmp_path: Path, sandbox: FakeSandbox, *, plan_only: str | None = None) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        journal=Journal(tmp_path / "journal.jsonl"),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        plan_phase_write_only_path=plan_only,
    )


def test_write_file_writes_into_sandbox(tmp_path: Path) -> None:
    box = FakeSandbox()
    out, is_err = dispatch(
        ALL_TOOLS,
        "write_file",
        {"path": "src/foo.py", "content": "print(1)\n"},
        _ctx(tmp_path, box),
        allowed=PHASE_EXECUTE_TOOLS,
    )
    assert is_err is False
    assert "wrote" in out
    assert box.files["src/foo.py"] == "print(1)\n"


def test_write_file_rejects_traversal(tmp_path: Path) -> None:
    box = FakeSandbox()
    _, is_err = dispatch(
        ALL_TOOLS,
        "write_file",
        {"path": "../evil", "content": "x"},
        _ctx(tmp_path, box),
        allowed=PHASE_EXECUTE_TOOLS,
    )
    assert is_err is True


def test_write_file_in_plan_phase_only_allows_plan_md(tmp_path: Path) -> None:
    box = FakeSandbox()
    ctx = _ctx(tmp_path, box, plan_only=".spek/plan.md")

    # The allowed path works.
    _, ok_err = dispatch(
        ALL_TOOLS,
        "write_file",
        {"path": ".spek/plan.md", "content": "# Plan\n\n1. [ ] do thing\n"},
        ctx,
        allowed=PHASE_PLAN_TOOLS,
    )
    assert ok_err is False
    assert ".spek/plan.md" in box.files

    # Any other path is rejected.
    out, is_err = dispatch(
        ALL_TOOLS,
        "write_file",
        {"path": "src/foo.py", "content": "x"},
        ctx,
        allowed=PHASE_PLAN_TOOLS,
    )
    assert is_err is True
    assert "plan phase" in out


def test_write_file_plan_phase_accepts_equivalent_paths(tmp_path: Path) -> None:
    box = FakeSandbox()
    ctx = _ctx(tmp_path, box, plan_only=".spek/plan.md")

    # `./.spek/plan.md` and `/work/.spek/plan.md` normalise to the same thing.
    for variant in ("./.spek/plan.md", "/work/.spek/plan.md"):
        _, is_err = dispatch(
            ALL_TOOLS,
            "write_file",
            {"path": variant, "content": "# Plan\n\n1. [ ] do thing\n"},
            ctx,
            allowed=PHASE_PLAN_TOOLS,
        )
        assert is_err is False, f"path {variant!r} should normalise to .spek/plan.md"


def test_write_file_validates_required_fields(tmp_path: Path) -> None:
    box = FakeSandbox()
    _, is_err = dispatch(
        ALL_TOOLS, "write_file", {"path": "x"}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )
    assert is_err is True
