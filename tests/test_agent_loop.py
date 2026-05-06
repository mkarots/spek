"""Tests for the three-phase agent loop.

These use a `FakeAnthropicClient` and a `FakeSandbox` so they run in
milliseconds without Docker or network. They cover:

- happy path: clarify -> plan -> execute -> termination green;
- phase markers are journaled in order;
- tool whitelists are forwarded to the LLM client per phase;
- step cap raises a clean failure;
- `--confirm-plan` abort returns blocked.
"""

from __future__ import annotations

import io
from pathlib import Path

from spek.agent import loop
from spek.config import CommandConfig
from spek.journal import Journal
from spek.languages.python import PythonProfile
from spek.sandbox.base import RunResult
from spek.spec import Spec
from tests.fakes import FakeAnthropicClient, FakeSandbox, text_response, tool_use_response

SPEC = """# Name
weather-tool

# Input
--city str

# Output
text

# Implementation
python
"""


def parse_spec(_text: str) -> Spec:
    """Construct the same Spec the LLM-backed parser would, without an LLM call.

    These tests exercise the agent loop, not the spec parser; building the
    Spec directly keeps them hermetic and fast.
    """
    return Spec(
        name="weather-tool",
        sections={
            "Name": "weather-tool",
            "Input": "--city str",
            "Output": "text",
            "Implementation": "python",
        },
        extras={},
    )


def _profile() -> PythonProfile:
    return PythonProfile()


def _cfg() -> CommandConfig:
    return CommandConfig.from_defaults(_profile(), "weather-tool")


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "journal.jsonl")


def _box_with_test_handlers(cfg: CommandConfig) -> FakeSandbox:
    """A sandbox whose `bash` handler returns success for build/test commands."""
    box = FakeSandbox()

    def predicate(argv: list[str]) -> bool:
        return argv[:2] == ["bash", "-lc"]

    def handler(argv: list[str]) -> RunResult:
        cmd = argv[2]
        if cmd.strip() == cfg.test_command.strip():
            return RunResult(
                exit_code=0,
                stdout="==== 1 passed in 0.01s ====\n",
                stderr="",
                duration_s=0.01,
            )
        if cmd.strip() == cfg.build_command.strip():
            return RunResult(exit_code=0, stdout="built\n", stderr="", duration_s=0.01)
        return RunResult(exit_code=0, stdout="", stderr="", duration_s=0.0)

    box.register(predicate, handler)
    return box


PLAN_BODY = (
    "# Plan\n\n"
    "1. [ ] Set up package skeleton\n"
    "2. [ ] Implement weather lookup\n"
    "3. [ ] Run build and tests and confirm green\n"
)


def test_happy_path_completes_through_all_three_phases(tmp_path: Path) -> None:
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    client = FakeAnthropicClient(
        responses=[
            # Phase 1: clarify -> assistant emits READY immediately.
            text_response("I have enough info.\nREADY"),
            # Phase 2: plan -> single write_file with the plan body.
            tool_use_response(
                "write_file",
                {"path": ".spek/plan.md", "content": PLAN_BODY},
                tool_use_id="p1",
            ),
            # End plan turn after tool result.
            text_response("Plan written."),
            # Phase 3: execute -> run build, then test.
            tool_use_response("bash", {"command": cfg.build_command}, tool_use_id="b1"),
            tool_use_response("bash", {"command": cfg.test_command}, tool_use_id="t1"),
            # Final assistant turn after termination is reached: end_turn with summary.
            text_response("Done."),
        ]
    )

    result = loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        max_steps=50,
        max_seconds=60,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )

    assert result.success is True, result.reason
    assert result.test_count == 1

    # Phase markers are journaled in order.
    phases = [e.content["phase"] for e in journal.read_all() if e.kind == "phase"]
    assert phases == ["clarify", "plan", "execute"]


def test_tool_whitelist_per_phase_is_passed_to_llm_client(tmp_path: Path) -> None:
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    client = FakeAnthropicClient(
        responses=[
            text_response("READY"),  # phase 1 ends immediately
            tool_use_response(
                "write_file",
                {"path": ".spek/plan.md", "content": PLAN_BODY},
                tool_use_id="p1",
            ),
            text_response("Plan written."),
            tool_use_response("bash", {"command": cfg.build_command}, tool_use_id="b1"),
            tool_use_response("bash", {"command": cfg.test_command}, tool_use_id="t1"),
            text_response("Done."),
        ]
    )

    loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        max_steps=50,
        max_seconds=60,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )

    # First call is phase 1 (clarify): only read/grep/epistemic.
    assert set(client.calls[0]["tools"]) == {"read_file", "grep", "epistemic"}
    # Phase 2 (plan) call: read/grep/write_file.
    plan_call = client.calls[1]
    assert set(plan_call["tools"]) == {"read_file", "grep", "write_file"}
    # Last call (execute) has all five.
    assert set(client.calls[-1]["tools"]) == {
        "read_file",
        "write_file",
        "grep",
        "bash",
        "epistemic",
    }


def test_step_cap_aborts_with_blocked_result(tmp_path: Path) -> None:
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    # Loop forever in clarify by never emitting READY and never asking; the
    # phase loop will hit the idle-turn cap. To force a step-cap hit instead,
    # script repeated tool_use calls that the cap counts.
    responses = []
    for i in range(50):
        responses.append(
            tool_use_response("read_file", {"path": "SPEC.md"}, tool_use_id=f"r{i}")
        )
    client = FakeAnthropicClient(responses=responses)
    box.files["SPEC.md"] = SPEC

    result = loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        max_steps=3,
        max_seconds=60,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )

    assert result.success is False
    assert "cap hit" in result.reason


def test_confirm_plan_abort_returns_blocked(tmp_path: Path) -> None:
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    client = FakeAnthropicClient(
        responses=[
            text_response("READY"),
            tool_use_response(
                "write_file",
                {"path": ".spek/plan.md", "content": PLAN_BODY},
                tool_use_id="p1",
            ),
            text_response("Plan written."),
            # No further responses scripted; we expect the loop to short-circuit
            # on user abort before reaching execute.
        ]
    )

    out = io.StringIO()
    in_ = io.StringIO("abort\n")

    result = loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        confirm_plan=True,
        max_steps=50,
        max_seconds=60,
        stdin=in_,
        stdout=out,
    )

    assert result.success is False
    assert "abort" in result.reason.lower()


def test_clarify_assistant_end_turn_does_not_send_assistant_prefill(tmp_path: Path) -> None:
    """Regression: model ends clarify turn without READY, loop must inject a user
    message before the next `client.create()` call. Otherwise the proxy returns
    400 ("This model does not support assistant message prefill.").
    """
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    client = FakeAnthropicClient(
        responses=[
            # Clarify turn 1: end_turn, no READY, no tool. Loop must nudge
            # (and inject a user continuation before re-entering create()).
            text_response("Thinking out loud, no question yet."),
            # Clarify turn 2: end_turn with READY on its own line -> phase done.
            # The READY token must be on its own line; `_has_ready_token` does
            # `line.strip() == "READY"` per-line, so "Got it. READY" wouldn't match.
            text_response("Got it.\nREADY"),
            # Plan: write the plan, then end the turn so is_plan_complete fires.
            tool_use_response(
                "write_file",
                {"path": ".spek/plan.md", "content": PLAN_BODY},
                tool_use_id="p1",
            ),
            text_response("Plan written."),
            # Execute: run build, run tests, then end the turn so termination
            # is checked. (is_phase_complete only fires on stop_reason=end_turn.)
            tool_use_response("bash", {"command": cfg.build_command}, tool_use_id="b1"),
            tool_use_response("bash", {"command": cfg.test_command}, tool_use_id="t1"),
            text_response("Done."),
        ]
    )

    result = loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        max_steps=50,
        max_seconds=60,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )

    assert result.success is True, result.reason

    # The 2nd clarify create() call must have a user message at the tail.
    second_clarify_call = client.calls[1]
    assert second_clarify_call["messages"][-1]["role"] == "user", (
        "Loop must inject a continuation user message after a non-READY "
        "end_turn — otherwise the conversation ends with an assistant turn "
        "and the LLM proxy rejects it."
    )


def test_resume_with_trailing_assistant_message_repairs_invariant(tmp_path: Path) -> None:
    """Regression: resuming a journal whose last entry is `kind=assistant`
    must inject a user continuation before the first LLM call, otherwise we
    re-trigger the assistant-prefill 400.
    """
    spec = parse_spec(SPEC)
    cfg = _cfg()
    journal = _journal(tmp_path)
    box = _box_with_test_handlers(cfg)

    # Pre-seed the journal: phase=clarify + a user seed + an assistant turn
    # that didn't get followed up (simulates a crash mid-clarify).
    journal.append_phase("clarify")
    journal.append("user", [{"type": "text", "text": "Read the spec and ask questions."}])
    journal.append("assistant", [{"type": "text", "text": "Mid-thought."}])

    client = FakeAnthropicClient(
        responses=[
            text_response("READY"),
            tool_use_response(
                "write_file",
                {"path": ".spek/plan.md", "content": PLAN_BODY},
                tool_use_id="p1",
            ),
            text_response("Plan written."),
            tool_use_response("bash", {"command": cfg.build_command}, tool_use_id="b1"),
            tool_use_response("bash", {"command": cfg.test_command}, tool_use_id="t1"),
            text_response("Done."),
        ]
    )

    loop.run(
        spec=spec,
        profile=_profile(),
        cfg=cfg,
        sandbox=box,
        journal=journal,
        client=client,
        plan_host_path=tmp_path / ".spek" / "plan.md",
        max_steps=50,
        max_seconds=60,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )

    first_resumed_call = client.calls[0]
    assert first_resumed_call["messages"][-1]["role"] == "user", (
        "Resume must repair the conversation invariant by appending a user "
        "message before re-entering the LLM."
    )
