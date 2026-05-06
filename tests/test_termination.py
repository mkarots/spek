"""Tests for spek.agent.termination."""

from __future__ import annotations

from pathlib import Path

from spek.agent import termination
from spek.config import CommandConfig
from spek.journal import Journal
from spek.languages.python import PythonProfile


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl")


def _record_bash(j: Journal, command: str, exit_code: int, stdout: str = "") -> None:
    j.append(
        "assistant",
        [
            {
                "type": "tool_use",
                "id": "t1",
                "name": "bash",
                "input": {"command": command},
            }
        ],
    )
    j.append(
        "tool_result",
        [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": f"exit_code={exit_code} duration=0.10s\n{stdout}",
            }
        ],
    )


def _cfg() -> CommandConfig:
    return CommandConfig.from_defaults(PythonProfile(), "weather-tool")


def test_done_when_build_and_test_green_with_one_test(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 0, stdout="==== 3 passed in 0.12s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is True
    assert status.test_count == 3


def test_not_done_when_build_failed(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.build_command, 1)
    _record_bash(j, cfg.test_command, 0, stdout="==== 3 passed in 0.12s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is False
    assert "build" in status.reason


def test_not_done_when_no_build_run(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.test_command, 0, stdout="==== 1 passed in 0.01s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is False
    assert "no recent build" in status.reason


def test_not_done_when_no_test_collected(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 0, stdout="==== no tests ran in 0.01s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is False
    assert "zero tests" in status.reason


def test_not_done_when_tests_failed(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 1, stdout="==== 1 failed in 0.01s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is False


def test_uses_most_recent_test_run_not_an_old_failing_one(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 1, stdout="==== 1 failed in 0.01s ====\n")
    # Agent fixed the bug and re-ran build+tests, both green.
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 0, stdout="==== 5 passed in 0.20s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is True
    assert status.test_count == 5


def test_irrelevant_bash_commands_are_ignored(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    cfg = _cfg()
    _record_bash(j, "ls -la", 0, stdout="hello\n")
    _record_bash(j, cfg.build_command, 0)
    _record_bash(j, cfg.test_command, 0, stdout="==== 2 passed in 0.10s ====\n")

    status = termination.check(j, cfg, PythonProfile())

    assert status.done is True
    assert status.test_count == 2
