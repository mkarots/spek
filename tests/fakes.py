"""Test doubles for spek sandbox + Anthropic client.

Kept here so they can be reused across multiple test files without a full
`conftest.py` pytest plugin.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from spek.agent.loop import LLMResponse
from spek.sandbox.base import RunResult, Sandbox, SandboxError
from spek.sandbox.docker import DockerSandbox


@dataclass
class FakeSandbox(Sandbox):
    """In-memory sandbox.

    Files live in a dict keyed by their normalised path relative to
    `WORK_DIR`. `run()` is dispatched via a registry of (predicate, handler)
    pairs the test sets up; an unhandled command raises so tests fail loudly
    rather than getting an empty success.
    """

    files: dict[str, str] = field(default_factory=dict)
    handlers: list[tuple[Callable[[list[str]], bool], Callable[[list[str]], RunResult]]] = field(
        default_factory=list
    )
    runs: list[list[str]] = field(default_factory=list)

    def __enter__(self) -> FakeSandbox:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def register(
        self,
        predicate: Callable[[list[str]], bool],
        handler: Callable[[list[str]], RunResult],
    ) -> None:
        self.handlers.append((predicate, handler))

    def run(self, argv: list[str], *, timeout: int = 120) -> RunResult:
        self.runs.append(list(argv))
        for pred, handler in self.handlers:
            if pred(argv):
                return handler(argv)
        raise AssertionError(f"FakeSandbox: unhandled run() with argv={argv!r}")

    def write_file(self, path: str, content: str) -> int:
        rel = DockerSandbox._validate_path(path)
        self.files[rel] = content
        return len(content.encode("utf-8"))

    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str:
        rel = DockerSandbox._validate_path(path)
        if rel not in self.files:
            raise SandboxError(f"read_file: {path!r} does not exist")
        body = self.files[rel]
        if len(body.encode("utf-8")) > max_bytes:
            return body[:max_bytes] + f"\n... [truncated; file exceeds {max_bytes} bytes]"
        return body

    def list_dir(self, path: str = ".") -> list[str]:
        rel = DockerSandbox._validate_path(path)
        prefix = "" if rel == "." else rel + "/"
        seen: set[str] = set()
        for k in self.files:
            if not k.startswith(prefix):
                continue
            tail = k[len(prefix) :]
            seen.add(tail.split("/", 1)[0])
        return sorted(seen)


@dataclass
class FakeAnthropicClient:
    """Plays back a scripted sequence of LLM responses.

    Each item in `responses` is consumed by one `create()` call. If the
    list is exhausted, an `AssertionError` is raised — tests should script
    enough turns to cover the path under test.

    Captured calls are stored in `calls` so tests can assert on what the
    loop actually sent (system prompts, tool whitelists, message history).
    """

    responses: list[LLMResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": [m for m in messages],
                "tools": [t["name"] for t in tools],
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("FakeAnthropicClient: no scripted responses left")
        return self.responses.pop(0)


def text_response(text: str) -> LLMResponse:
    return LLMResponse(content=[{"type": "text", "text": text}], stop_reason="end_turn")


def tool_use_response(name: str, input: dict[str, Any], *, tool_use_id: str = "t1") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "tool_use", "id": tool_use_id, "name": name, "input": input}],
        stop_reason="tool_use",
    )


def spec_response(
    name: str,
    *,
    input: str = "x",
    output: str = "y",
    implementation: str = "python",
    extras: dict[str, str] | None = None,
) -> LLMResponse:
    """Build an `emit_spec` tool_use response for the LLM-backed spec parser."""
    return tool_use_response(
        "emit_spec",
        {
            "name": name,
            "sections": {
                "Name": name,
                "Input": input,
                "Output": output,
                "Implementation": implementation,
            },
            "extras": extras or {},
        },
        tool_use_id="spec1",
    )
