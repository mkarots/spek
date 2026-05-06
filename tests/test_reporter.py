"""Tests for the progress reporter.

The reporter is observable behaviour: a small, stable line format that
users will (informally) grep against. We exercise:
- every event method writes a line
- truncation kicks in for long inputs
- failures in the underlying stream don't crash the run
- `summarise_tool_input` picks the meaningful field per tool
"""

from __future__ import annotations

import io

import pytest

from spek.agent.reporter import (
    ConsoleReporter,
    NullReporter,
    summarise_tool_input,
    summarise_tool_result,
)


def _lines(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line.strip()]


def test_console_reporter_emits_one_line_per_event() -> None:
    out = io.StringIO()
    rep = ConsoleReporter(stream=out)

    rep.start(spec_name="spec.md", workdir="/tmp/x", model="m")
    rep.phase("clarify")
    rep.llm_turn(phase="clarify", turn=1)
    rep.tool_call(name="bash", summary="echo hi")
    rep.tool_result(name="bash", ok=True, summary="hi")
    rep.tool_result(name="bash", ok=False, summary="boom")
    rep.nudge(phase="plan", reason="write the plan")
    rep.phase_done(phase="plan", turns=3)
    rep.finished(success=True, reason="all green", test_count=2)
    rep.finished(success=False, reason="cap hit", test_count=0)

    lines = _lines(out)
    assert len(lines) == 10
    assert "build start" in lines[0]
    assert "phase clarify" in lines[1]
    assert "LLM turn 1" in lines[2]
    assert "tool bash(echo hi)" in lines[3]
    assert "bash ok: hi" in lines[4]
    assert "bash ERR: boom" in lines[5]
    assert "phase plan done (3 turns)" in lines[7]
    assert "all green" in lines[8] and "tests passed: 2" in lines[8]
    assert "cap hit" in lines[9]


def test_console_reporter_truncates_long_summaries() -> None:
    out = io.StringIO()
    rep = ConsoleReporter(stream=out)

    long = "x" * 300
    rep.tool_call(name="bash", summary=long)

    line = _lines(out)[0]
    # 100 char cap + ellipsis; line itself is longer because of prefix/timestamp.
    assert "\u2026" in line
    assert "x" * 200 not in line


def test_console_reporter_collapses_whitespace_in_summary() -> None:
    out = io.StringIO()
    rep = ConsoleReporter(stream=out)

    rep.tool_call(name="bash", summary="echo  hello\nworld\t!")

    line = _lines(out)[0]
    assert "echo hello world !" in line
    assert "\n" not in line.removesuffix("\n")  # no embedded newlines in the line itself


def test_console_reporter_swallows_stream_errors() -> None:
    """A broken stream (e.g. closed pipe) must not crash the agent."""

    class Broken(io.StringIO):
        def write(self, _s: str) -> int:
            raise OSError("pipe closed")

    rep = ConsoleReporter(stream=Broken())

    # Should not raise.
    rep.phase("clarify")
    rep.tool_call(name="bash", summary="ls")
    rep.finished(success=True, reason="ok", test_count=1)


def test_null_reporter_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    rep = NullReporter()
    rep.start(spec_name="x", workdir="y", model="z")
    rep.phase("clarify")
    rep.tool_call(name="bash", summary="echo")
    rep.finished(success=True, reason="ok", test_count=1)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    ("name", "args", "expected_substring"),
    [
        ("bash", {"command": "uv run pytest"}, "uv run pytest"),
        ("read_file", {"path": "src/x.py"}, "src/x.py"),
        ("write_file", {"path": "a.py", "content": "x" * 50}, "a.py (50 bytes)"),
        ("grep", {"pattern": "foo", "path": "src"}, "'foo' in src"),
        ("epistemic", {"question": "what about Y?"}, "what about Y?"),
        ("unknown", {"k": 1}, '"k":1'),
    ],
)
def test_summarise_tool_input_picks_meaningful_field(
    name: str, args: dict, expected_substring: str
) -> None:
    assert expected_substring in summarise_tool_input(name, args)


def test_summarise_tool_result_handles_empty_and_multiline() -> None:
    assert summarise_tool_result("") == "<empty>"
    assert summarise_tool_result("first\nsecond") == "first"
    assert summarise_tool_result("oneline") == "oneline"
