"""Tests for the bash tool."""

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


def test_bash_returns_combined_output_and_exit_code(tmp_path: Path) -> None:
    box = FakeSandbox()
    box.register(
        lambda argv: argv[:2] == ["bash", "-lc"],
        lambda _argv: RunResult(exit_code=0, stdout="hi\n", stderr="", duration_s=0.01),
    )

    out, is_err = dispatch(
        ALL_TOOLS,
        "bash",
        {"command": "echo hi"},
        _ctx(tmp_path, box),
        allowed=PHASE_EXECUTE_TOOLS,
    )

    assert is_err is False
    assert "exit_code=0" in out
    assert "hi" in out


def test_bash_caps_timeout_at_max(tmp_path: Path) -> None:
    box = FakeSandbox()
    seen_timeouts: list[int] = []

    def predicate(argv: list[str]) -> bool:
        return argv[:2] == ["bash", "-lc"]

    def handler(_argv: list[str]) -> RunResult:
        return RunResult(exit_code=0, stdout="", stderr="", duration_s=0.0)

    # Wrap the FakeSandbox to capture the timeout argument.
    real_run = box.run

    def spy_run(argv: list[str], *, timeout: int = 120) -> RunResult:
        seen_timeouts.append(timeout)
        return real_run(argv, timeout=timeout)

    box.register(predicate, handler)
    box.run = spy_run  # type: ignore[method-assign]

    dispatch(
        ALL_TOOLS,
        "bash",
        {"command": "sleep 10", "timeout": 99999},
        _ctx(tmp_path, box),
        allowed=PHASE_EXECUTE_TOOLS,
    )

    assert seen_timeouts and seen_timeouts[0] <= 600


def test_bash_rejects_empty_command(tmp_path: Path) -> None:
    box = FakeSandbox()
    _, is_err = dispatch(
        ALL_TOOLS, "bash", {"command": "  "}, _ctx(tmp_path, box), allowed=PHASE_EXECUTE_TOOLS
    )
    assert is_err is True
