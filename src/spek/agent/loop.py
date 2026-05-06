"""The three-phase Anthropic native tool-use loop.

Phase 1 (Clarify) -> Phase 2 (Plan) -> Phase 3 (Execute).

The loop is provider-aware (we use the Anthropic SDK directly) but the
client is injected via `LLMClient` so tests can pass a scripted fake.

Resume model: every assistant/user/tool_result message is journaled
verbatim, and a `phase` marker is journaled at every transition. On a
fresh start, all three phases run in sequence; on resume, we read the
journal and continue inside the most recent phase marker.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from spek.agent import planning, prompts, termination
from spek.agent.reporter import (
    NullReporter,
    Reporter,
    summarise_tool_input,
    summarise_tool_result,
)
from spek.config import CommandConfig
from spek.journal import Journal
from spek.languages.base import LanguageProfile
from spek.sandbox.base import Sandbox
from spek.spec import Spec
from spek.tools import (
    ALL_TOOLS,
    ANTHROPIC_TOOL_SCHEMAS,
    PHASE_CLARIFY_TOOLS,
    PHASE_EXECUTE_TOOLS,
    PHASE_PLAN_TOOLS,
    ToolContext,
    dispatch,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "evals-anthropic/claude-sonnet-4-7"
DEFAULT_MAX_TOKENS = 4096
READY_TOKEN = "READY"
NUDGE_AFTER_IDLE_TURNS = 3
MAX_CLARIFY_TURNS = 12


class StopReason(str):
    pass


# ---------------------------------------------------------------------------
# LLM client interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """The subset of `messages.create` we care about."""

    content: list[dict[str, Any]]  # assistant content blocks
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | ...


class LLMClient(Protocol):
    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse: ...


class AnthropicLLMClient:
    """Production client backed by `anthropic.Anthropic`."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        from anthropic import Anthropic  # imported lazily so tests don't need the SDK

        self._client = Anthropic()
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        msg = self._client.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
        # Convert SDK content blocks to plain dicts so the journal stays
        # provider-agnostic and JSON-serialisable.
        blocks: list[dict[str, Any]] = []
        for b in msg.content:
            if hasattr(b, "model_dump"):
                blocks.append(b.model_dump())
            elif isinstance(b, dict):
                blocks.append(b)
            else:  # pragma: no cover - defensive
                blocks.append({"type": "text", "text": str(b)})
        return LLMResponse(content=blocks, stop_reason=msg.stop_reason or "end_turn")


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


class CapHit(RuntimeError):
    """Step or wall-clock cap reached."""


@dataclass
class Caps:
    max_steps: int
    max_seconds: int
    started_at: float

    def step(self, steps_so_far: int) -> None:
        if steps_so_far > self.max_steps:
            raise CapHit(f"step cap hit ({self.max_steps})")
        if time.monotonic() - self.started_at > self.max_seconds:
            raise CapHit(f"time cap hit ({self.max_seconds}s)")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    success: bool
    reason: str
    test_count: int = 0


class AgentAborted(RuntimeError):
    """User aborted the plan-confirm gate."""


class AgentBlocked(RuntimeError):
    """The agent ended its turn cleanly without satisfying termination."""


def _content_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _has_ready_token(blocks: list[dict[str, Any]]) -> bool:
    text = _content_text(blocks)
    return any(line.strip() == READY_TOKEN for line in text.splitlines())


def _tool_uses(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in blocks if b.get("type") == "tool_use"]


def _seed_user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


_CONTINUATION_PER_PHASE: dict[str, str] = {
    "clarify": (
        "You ended your turn without the literal token READY and without using a "
        "tool. If you have all the information you need, end your next turn with "
        f"a short summary and the literal token {READY_TOKEN} on its own line. "
        "Otherwise, ask the next clarifying question with the `epistemic` tool."
    ),
    "plan": (
        "You ended your turn without writing the plan. Call `write_file` with the "
        "plan body now."
    ),
    "execute": (
        "You ended your turn but the build/test termination criteria are not met "
        "yet. Continue with the next open step."
    ),
}


def _continuation_user_message(phase: str) -> dict[str, Any]:
    """A safe default user follow-up for any phase.

    Used when the model emits an `end_turn` that does not satisfy the
    phase's completion check and no phase-specific nudge is available.
    Without this we would re-enter `client.create()` with an assistant
    message at the tail of the conversation, which the LLM proxy rejects
    with HTTP 400 ("This model does not support assistant message
    prefill").
    """
    text = _CONTINUATION_PER_PHASE.get(
        phase,
        "Please continue. End your turn with a tool call or a clear next action.",
    )
    return _seed_user_message(text)


def _build_tool_result_blocks(
    tool_uses: list[dict[str, Any]],
    ctx: ToolContext,
    *,
    allowed: frozenset[str],
    reporter: Reporter,
) -> list[dict[str, Any]]:
    """Run each tool_use in order, return the matching tool_result blocks."""
    out: list[dict[str, Any]] = []
    for tu in tool_uses:
        name = tu.get("name", "")
        args = tu.get("input") or {}
        reporter.tool_call(name=name, summary=summarise_tool_input(name, args))
        result_text, is_error = dispatch(
            ALL_TOOLS,
            name,
            args,
            ctx,
            allowed=allowed,
        )
        reporter.tool_result(name=name, ok=not is_error, summary=summarise_tool_result(result_text))
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tu.get("id"),
            "content": result_text,
        }
        if is_error:
            block["is_error"] = True
        out.append(block)
    return out


def _run_phase_loop(
    *,
    phase: str,
    system_prompt: str,
    seed_user: dict[str, Any] | None,
    journal: Journal,
    client: LLMClient,
    model: str,
    ctx: ToolContext,
    allowed: frozenset[str],
    caps: Caps,
    is_phase_complete,
    reporter: Reporter,
    on_idle_turn=None,
    max_turns: int | None = None,
) -> int:
    """Drive one phase to completion. Returns the number of LLM turns it took.

    `is_phase_complete(assistant_blocks, idle_turns)` is called after each
    assistant turn whose `stop_reason == 'end_turn'`. If it returns truthy,
    the phase is over.

    Loop invariant: before every call to `client.create`, `msgs[-1]` is a
    `role=user` block (Anthropic-compatible models reject conversations
    that end with an assistant turn — the proxy returns
    "This model does not support assistant message prefill."). We enforce
    this in two places:
      1. After a non-tool-use assistant turn we either terminate the phase
         or append a user nudge. If `on_idle_turn` is not provided we
         synthesise a generic continuation prompt so we never re-enter
         `create()` with a trailing assistant.
      2. On entry (handles resume from a journal whose last entry is an
         assistant turn) we inject a continuation prompt up front.
    """
    msgs = list(journal.messages())
    if seed_user is not None and not msgs:
        journal.append("user", seed_user["content"])
        msgs = list(journal.messages())
    elif msgs and msgs[-1]["role"] == "assistant":
        # Resume edge case: the previous run journaled an assistant turn
        # last (e.g. crashed before the user-side reply). Restore the
        # invariant before calling the LLM.
        resume_nudge = _continuation_user_message(phase)
        journal.append("user", resume_nudge["content"])
        msgs.append(resume_nudge)

    idle_turns = 0
    turns = 0
    steps_so_far = 0
    while True:
        caps.step(steps_so_far)
        turns += 1
        if max_turns is not None and turns > max_turns:
            return turns - 1
        reporter.llm_turn(phase=phase, turn=turns)
        resp = client.create(
            model=model,
            system=system_prompt,
            messages=msgs,
            tools=[s for s in ANTHROPIC_TOOL_SCHEMAS if s["name"] in allowed],
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        journal.append("assistant", resp.content)
        msgs.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "tool_use":
            tool_uses = _tool_uses(resp.content)
            results = _build_tool_result_blocks(tool_uses, ctx, allowed=allowed, reporter=reporter)
            journal.append("tool_result", results)
            msgs.append({"role": "user", "content": results})
            # `epistemic` calls don't count against the step cap (per plan).
            non_epistemic = [tu for tu in tool_uses if tu.get("name") != "epistemic"]
            steps_so_far += len(non_epistemic)
            idle_turns = 0
            continue

        # stop_reason == "end_turn" (or anything else): no tool_use this turn.
        idle_turns += 1
        if is_phase_complete(resp.content, idle_turns):
            return turns

        # Maintain the invariant: append a user message before continuing.
        nudge = on_idle_turn(resp.content, idle_turns) if on_idle_turn is not None else None
        if nudge is None:
            nudge = _continuation_user_message(phase)
        reporter.nudge(phase=phase, reason=_content_text(nudge["content"]))
        journal.append("user", nudge["content"])
        msgs.append(nudge)


# ---------------------------------------------------------------------------
# Phase orchestration
# ---------------------------------------------------------------------------


def _resume_phase(journal: Journal) -> str:
    return journal.current_phase() or "clarify"


def run(
    *,
    spec: Spec,
    profile: LanguageProfile,
    cfg: CommandConfig,
    sandbox: Sandbox,
    journal: Journal,
    client: LLMClient,
    model: str = DEFAULT_MODEL,
    plan_host_path: Path,
    confirm_plan: bool = False,
    max_steps: int = 120,
    max_seconds: int = 30 * 60,
    stdin: io.TextIOBase | None = None,
    stdout: io.TextIOBase | None = None,
    reporter: Reporter | None = None,
) -> AgentResult:
    """Run the three-phase agent loop. Returns an `AgentResult`."""
    import sys

    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    reporter = reporter or NullReporter()

    caps = Caps(max_steps=max_steps, max_seconds=max_seconds, started_at=time.monotonic())

    current = _resume_phase(journal)

    def _finish(result: AgentResult) -> AgentResult:
        reporter.finished(
            success=result.success, reason=result.reason, test_count=result.test_count
        )
        return result

    # ---- Phase 1: Clarify --------------------------------------------------
    if current == "clarify":
        journal.append_phase("clarify")
        reporter.phase("clarify")
        sys_prompt = prompts.system_clarify(spec, cfg)
        seed = _seed_user_message(
            "Read the spec above and use the `epistemic` tool to ask any clarifying "
            "questions you need. When you have enough information, end your turn with "
            f"a short summary and the literal token {READY_TOKEN} on its own line."
        )
        clarify_ctx = ToolContext(sandbox=sandbox, journal=journal, stdin=stdin, stdout=stdout)

        def _clarify_complete(blocks: list[dict[str, Any]], idle_turns: int) -> bool:
            return _has_ready_token(blocks) or idle_turns >= NUDGE_AFTER_IDLE_TURNS

        try:
            turns = _run_phase_loop(
                phase="clarify",
                system_prompt=sys_prompt,
                seed_user=seed,
                journal=journal,
                client=client,
                model=model,
                ctx=clarify_ctx,
                allowed=PHASE_CLARIFY_TOOLS,
                caps=caps,
                is_phase_complete=_clarify_complete,
                reporter=reporter,
                max_turns=MAX_CLARIFY_TURNS,
            )
        except CapHit as exc:
            return _finish(AgentResult(False, f"cap hit during clarify: {exc}"))
        reporter.phase_done(phase="clarify", turns=turns)
        current = "plan"

    # ---- Phase 2: Plan -----------------------------------------------------
    if current == "plan":
        journal.append_phase("plan")
        reporter.phase("plan")
        sys_prompt = prompts.system_plan(spec, cfg, planning.PLAN_PATH)
        seed = _seed_user_message(
            f"Write the execution plan to `{planning.PLAN_PATH}` using the `write_file` tool. "
            "Follow the format strictly; the plan must parse into a numbered checklist."
        )
        plan_ctx = ToolContext(
            sandbox=sandbox,
            journal=journal,
            stdin=stdin,
            stdout=stdout,
            plan_phase_write_only_path=planning.PLAN_PATH,
        )

        def _plan_complete(_blocks: list[dict[str, Any]], _idle: int) -> bool:
            try:
                planning.load_from_sandbox(sandbox)
                return True
            except Exception:
                return False

        def _plan_nudge(_blocks: list[dict[str, Any]], _idle: int) -> dict[str, Any]:
            return _seed_user_message(
                f"You must call write_file with path={planning.PLAN_PATH!r} and a body that "
                "matches the required numbered-checklist format (1. [ ] step title)."
            )

        try:
            turns = _run_phase_loop(
                phase="plan",
                system_prompt=sys_prompt,
                seed_user=seed,
                journal=journal,
                client=client,
                model=model,
                ctx=plan_ctx,
                allowed=PHASE_PLAN_TOOLS,
                caps=caps,
                is_phase_complete=_plan_complete,
                reporter=reporter,
                on_idle_turn=_plan_nudge,
            )
        except CapHit as exc:
            return _finish(AgentResult(False, f"cap hit during plan: {exc}"))
        reporter.phase_done(phase="plan", turns=turns)

        if confirm_plan:
            plan_text = sandbox.read_file(planning.PLAN_PATH)
            decision = planning.confirm_plan_interactively(
                plan_text,
                stdin=stdin,
                stdout=stdout,
                plan_host_path=plan_host_path,
            )
            if decision == "abort":
                return _finish(AgentResult(False, "user aborted at plan-confirm gate"))
            if decision == "edit":
                # User edited plan_host_path on the host; re-validate before continuing.
                # The bind-mount means the in-container view is already updated.
                try:
                    planning.load_from_sandbox(sandbox)
                except Exception as exc:
                    return _finish(AgentResult(False, f"edited plan does not parse: {exc}"))
        current = "execute"

    # ---- Phase 3: Execute --------------------------------------------------
    if current == "execute":
        journal.append_phase("execute")
        reporter.phase("execute")
        sys_prompt = prompts.system_execute(spec, cfg, planning.PLAN_PATH)
        seed = _seed_user_message(
            f"Implement the package by working `{planning.PLAN_PATH}` top-to-bottom. "
            "Mark steps `[x]` as you complete them. Stop when build and tests are green."
        )
        exec_ctx = ToolContext(sandbox=sandbox, journal=journal, stdin=stdin, stdout=stdout)

        def _execute_complete(_blocks: list[dict[str, Any]], _idle: int) -> bool:
            status = termination.check(journal, cfg, profile)
            return status.done

        def _execute_nudge(_blocks: list[dict[str, Any]], _idle: int) -> dict[str, Any]:
            try:
                steps = planning.load_from_sandbox(sandbox)
                nxt = planning.first_open_step(steps)
                if nxt is not None:
                    return _seed_user_message(
                        f"You ended your turn but build/tests are not green yet. "
                        f"Continue with step {nxt.index}: {nxt.title}."
                    )
            except Exception:
                pass
            return _seed_user_message(
                "You ended your turn but the termination criteria are not met yet "
                "(build exit 0 AND test exit 0 AND >=1 test collected). "
                f"Run `{cfg.build_command}` and `{cfg.test_command}` and continue."
            )

        try:
            turns = _run_phase_loop(
                phase="execute",
                system_prompt=sys_prompt,
                seed_user=seed,
                journal=journal,
                client=client,
                model=model,
                ctx=exec_ctx,
                allowed=PHASE_EXECUTE_TOOLS,
                caps=caps,
                is_phase_complete=_execute_complete,
                reporter=reporter,
                on_idle_turn=_execute_nudge,
            )
        except CapHit as exc:
            return _finish(AgentResult(False, f"cap hit during execute: {exc}"))
        reporter.phase_done(phase="execute", turns=turns)

        status = termination.check(journal, cfg, profile)
        if status.done:
            return _finish(AgentResult(True, status.reason, test_count=status.test_count))
        return _finish(
            AgentResult(False, f"agent ended without satisfying termination: {status.reason}")
        )

    return _finish(AgentResult(False, f"unknown phase: {current!r}"))
