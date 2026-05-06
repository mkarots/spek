"""Append-only JSONL journal.

Every meaningful event in a `spek build` run is written here:

- `kind="user"|"assistant"`: a full Anthropic message (content blocks verbatim)
- `kind="tool_result"`: the result of a tool call (mirrors the user-message
  content block we send back to the model on the next turn)
- `kind="phase"`: a transition marker, content `{"phase": "clarify"|"plan"|"execute"}`

The "program counter" is just `len(read_all())` — there is no separate state.
Resume = read the file, reconstruct the messages list, infer the active phase
from the last `phase` marker, and continue.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

EntryKind = Literal["user", "assistant", "tool_result", "phase"]
Phase = Literal["clarify", "plan", "execute"]


@dataclass(frozen=True)
class Entry:
    """A single journal record."""

    ts: str
    kind: EntryKind
    content: Any

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "content": self.content}


class Journal:
    """Append-only JSONL log with crash-resistant reads.

    All writes are flushed and `fsync`-ed so the file is durable up to the
    last fully-written line. On read, a trailing partial line is dropped
    rather than crashing the process — that path is exercised in tests via
    `read_all(strict=False)`.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, kind: EntryKind, content: Any) -> Entry:
        """Append an entry; returns the entry that was written."""
        entry = Entry(
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            kind=kind,
            content=content,
        )
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def append_phase(self, phase: Phase) -> Entry:
        """Convenience: write a phase-transition marker."""
        return self.append("phase", {"phase": phase})

    def read_all(self) -> list[Entry]:
        """Return all journal entries in order, dropping a trailing partial line.

        We choose tolerance over strictness: a half-written final line is
        almost always a crash artefact, and refusing to resume would be
        worse than dropping that line.
        """
        if not self._path.exists():
            return []
        entries: list[Entry] = []
        with self._path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        for i, raw in enumerate(lines):
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                # Tolerate only a trailing partial line; mid-file corruption
                # is a real bug and worth surfacing.
                if i == len(lines) - 1:
                    break
                raise
            entries.append(Entry(ts=obj["ts"], kind=obj["kind"], content=obj["content"]))
        return entries

    @property
    def pc(self) -> int:
        """Program counter: number of entries currently in the journal."""
        return len(self.read_all())

    def current_phase(self) -> Phase | None:
        """Return the most recent phase marker, or None if there are none."""
        last: Phase | None = None
        for entry in self.read_all():
            if entry.kind == "phase":
                last = entry.content["phase"]
        return last

    def messages(self) -> list[dict[str, Any]]:
        """Reconstruct the Anthropic `messages` list from the journal.

        Phase markers are not messages; they are skipped. The caller is
        responsible for prepending the appropriate system prompt for the
        current phase.
        """
        msgs: list[dict[str, Any]] = []
        for entry in self.read_all():
            if entry.kind == "phase":
                continue
            if entry.kind == "user":
                msgs.append({"role": "user", "content": entry.content})
            elif entry.kind == "assistant":
                msgs.append({"role": "assistant", "content": entry.content})
            elif entry.kind == "tool_result":
                # Tool results are sent back to the model as a user message
                # with a `tool_result` content block. We store them as their
                # final wire shape so resume is a straight passthrough.
                msgs.append({"role": "user", "content": entry.content})
        return msgs

    def has_unmatched_tool_use(self) -> bool:
        """True if the last assistant turn issued tool_use blocks without
        matching tool_result entries — i.e. we crashed mid-tool.

        Resume callers should re-run those tool calls before the next LLM
        turn so the conversation is well-formed.
        """
        entries = self.read_all()
        # Walk backwards to find the last assistant entry; check whether
        # subsequent entries cover its tool_use ids.
        last_assistant_idx: int | None = None
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "assistant":
                last_assistant_idx = i
                break
        if last_assistant_idx is None:
            return False
        tool_use_ids = _tool_use_ids(entries[last_assistant_idx].content)
        if not tool_use_ids:
            return False
        seen: set[str] = set()
        for e in entries[last_assistant_idx + 1 :]:
            if e.kind == "tool_result":
                seen.update(_tool_result_ids(e.content))
        return not tool_use_ids.issubset(seen)


def _tool_use_ids(content: Any) -> set[str]:
    """Extract `tool_use` ids from an assistant content list."""
    out: set[str] = set()
    if isinstance(content, Iterable) and not isinstance(content, (str, bytes)):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tu_id = block.get("id")
                if isinstance(tu_id, str):
                    out.add(tu_id)
    return out


def _tool_result_ids(content: Any) -> set[str]:
    """Extract `tool_use_id`s from a tool_result content list."""
    out: set[str] = set()
    if isinstance(content, Iterable) and not isinstance(content, (str, bytes)):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ru_id = block.get("tool_use_id")
                if isinstance(ru_id, str):
                    out.add(ru_id)
    return out
