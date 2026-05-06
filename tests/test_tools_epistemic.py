"""Tests for the epistemic tool."""

from __future__ import annotations

import io
from pathlib import Path

from spek.journal import Journal
from spek.tools import ALL_TOOLS, PHASE_CLARIFY_TOOLS, ToolContext, dispatch
from tests.fakes import FakeSandbox


def _ctx(tmp_path: Path, *, stdin: str) -> ToolContext:
    return ToolContext(
        sandbox=FakeSandbox(),
        journal=Journal(tmp_path / "journal.jsonl"),
        stdin=io.StringIO(stdin),
        stdout=io.StringIO(),
    )


def test_epistemic_prompts_and_returns_user_answer(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, stdin="Celsius\n")

    out, is_err = dispatch(
        ALL_TOOLS,
        "epistemic",
        {
            "question": "Default temperature unit?",
            "why_blocked": "spec doesn't say",
            "how_to_resolve": "C or F",
        },
        ctx,
        allowed=PHASE_CLARIFY_TOOLS,
    )

    assert is_err is False
    assert out == "Celsius"
    rendered = ctx.stdout.getvalue()
    assert "Default temperature unit?" in rendered
    assert "Why blocked" in rendered


def test_epistemic_handles_empty_first_line_then_multiline(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, stdin="\nLine 1\nLine 2\n")

    out, _ = dispatch(
        ALL_TOOLS,
        "epistemic",
        {"question": "Q", "why_blocked": "w", "how_to_resolve": "h"},
        ctx,
        allowed=PHASE_CLARIFY_TOOLS,
    )

    assert out == "Line 1\nLine 2"


def test_epistemic_no_input_at_all_returns_placeholder(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, stdin="")

    out, _ = dispatch(
        ALL_TOOLS,
        "epistemic",
        {"question": "Q", "why_blocked": "w", "how_to_resolve": "h"},
        ctx,
        allowed=PHASE_CLARIFY_TOOLS,
    )

    assert "no answer" in out.lower()


def test_epistemic_rejects_empty_question(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, stdin="x\n")
    _, is_err = dispatch(
        ALL_TOOLS,
        "epistemic",
        {"question": "  ", "why_blocked": "", "how_to_resolve": ""},
        ctx,
        allowed=PHASE_CLARIFY_TOOLS,
    )
    assert is_err is True
