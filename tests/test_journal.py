"""Tests for spek.journal."""

from __future__ import annotations

from pathlib import Path

from spek.journal import Journal


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")

    j.append("user", [{"type": "text", "text": "hello"}])
    j.append("assistant", [{"type": "text", "text": "hi"}])

    entries = j.read_all()
    assert [e.kind for e in entries] == ["user", "assistant"]
    assert entries[0].content[0]["text"] == "hello"


def test_pc_equals_line_count(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    assert j.pc == 0
    j.append("user", "x")
    j.append("assistant", "y")
    j.append_phase("clarify")
    assert j.pc == 3


def test_phase_marker_round_trip(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")

    j.append_phase("clarify")
    j.append("user", "x")
    j.append_phase("plan")

    assert j.current_phase() == "plan"


def test_current_phase_is_none_when_no_markers(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append("user", "x")
    assert j.current_phase() is None


def test_messages_skips_phase_markers(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append_phase("clarify")
    j.append("user", [{"type": "text", "text": "u"}])
    j.append("assistant", [{"type": "text", "text": "a"}])
    j.append_phase("plan")

    msgs = j.messages()

    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_tool_result_is_emitted_as_user_role(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append("assistant", [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}])
    j.append(
        "tool_result",
        [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
    )

    msgs = j.messages()

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"][0]["type"] == "tool_result"


def test_partial_trailing_line_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    j = Journal(path)
    j.append("user", "x")
    # Simulate a crash mid-write.
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts":"2026-04-25","kind":"assist')

    entries = j.read_all()
    assert len(entries) == 1


def test_has_unmatched_tool_use_detects_mid_tool_crash(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append(
        "assistant",
        [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "bash", "input": {}},
        ],
    )
    j.append("tool_result", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])

    assert j.has_unmatched_tool_use() is True


def test_has_unmatched_tool_use_false_when_all_resolved(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append("assistant", [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}])
    j.append("tool_result", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])
    assert j.has_unmatched_tool_use() is False


def test_has_unmatched_tool_use_false_when_no_tool_uses(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.append("assistant", [{"type": "text", "text": "done"}])
    assert j.has_unmatched_tool_use() is False


def test_read_all_creates_no_file(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    assert j.read_all() == []
    assert not (tmp_path / "journal.jsonl").exists()
