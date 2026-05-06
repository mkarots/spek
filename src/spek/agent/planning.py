"""`.spek/plan.md` parsing, rendering, and the `--confirm-plan` gate.

The plan file is the single contract between phase 2 and phase 3. It must
be parseable into a list of `PlanStep`s; the agent loop refuses to leave
phase 2 until that's the case.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from spek.sandbox.base import Sandbox

PLAN_PATH = ".spek/plan.md"


class PlanParseError(ValueError):
    """The plan file does not match the expected numbered-checklist format."""


@dataclass(frozen=True)
class PlanStep:
    index: int
    title: str
    done: bool
    details: str | None


_STEP_RE = re.compile(r"^(\d+)\.\s+\[(x| )\]\s+(.*?)\s*$")


def parse(text: str) -> list[PlanStep]:
    """Parse the body of a plan.md into a list of `PlanStep`s.

    Raises `PlanParseError` if no numbered items are found, numbering is
    non-monotonic (must be 1,2,3,...), or a numbered line is missing the
    `[ ]`/`[x]` checkbox.
    """
    steps: list[PlanStep] = []
    current_details: list[str] = []
    expected_idx = 1

    lines = text.splitlines()
    for raw in lines:
        line = raw.rstrip()
        # Numbered-line detection: `\d+. ` at the start of the line. We catch
        # "missing checkbox" cases too, to give a more useful error than
        # silently skipping the line.
        if re.match(r"^\d+\.\s", line):
            m = _STEP_RE.match(line)
            if not m:
                raise PlanParseError(
                    f"numbered line is missing a `[ ]` or `[x]` checkbox: {line!r}"
                )
            idx = int(m.group(1))
            if idx != expected_idx:
                raise PlanParseError(
                    f"plan numbering is non-monotonic: expected step {expected_idx}, got {idx}"
                )
            # Attach previously-collected detail lines to the *previous* step.
            if steps and current_details:
                last = steps[-1]
                steps[-1] = PlanStep(
                    index=last.index,
                    title=last.title,
                    done=last.done,
                    details="\n".join(current_details).strip() or None,
                )
                current_details = []
            steps.append(
                PlanStep(
                    index=idx,
                    title=m.group(3).strip(),
                    done=m.group(2) == "x",
                    details=None,
                )
            )
            expected_idx += 1
        elif steps:
            # Indented or bullet detail lines belong to the most recent step.
            stripped = line.strip()
            if stripped:
                current_details.append(stripped)
    # Flush any trailing details onto the last step.
    if steps and current_details:
        last = steps[-1]
        steps[-1] = PlanStep(
            index=last.index,
            title=last.title,
            done=last.done,
            details="\n".join(current_details).strip() or None,
        )

    if not steps:
        raise PlanParseError("plan.md must contain at least one numbered `[ ]` step")
    return steps


def render(steps: list[PlanStep], *, header: str = "# Plan") -> str:
    """Render `steps` back to the canonical plan.md format."""
    out = [header, ""]
    for s in steps:
        box = "x" if s.done else " "
        out.append(f"{s.index}. [{box}] {s.title}")
        if s.details:
            for line in s.details.splitlines():
                out.append(f"   - {line}")
    return "\n".join(out) + "\n"


def mark_done(steps: list[PlanStep], index: int) -> list[PlanStep]:
    """Return a new list with step `index` marked `[x]`."""
    out: list[PlanStep] = []
    for s in steps:
        out.append(
            PlanStep(
                index=s.index,
                title=s.title,
                done=s.done or s.index == index,
                details=s.details,
            )
        )
    return out


def first_open_step(steps: list[PlanStep]) -> PlanStep | None:
    return next((s for s in steps if not s.done), None)


def load_from_sandbox(sandbox: Sandbox) -> list[PlanStep]:
    """Read `.spek/plan.md` from the sandbox and parse it.

    Raises `PlanParseError` (passthrough from `parse()`) on malformed plans
    or `ToolError`-equivalent sandbox failures.
    """
    text = sandbox.read_file(PLAN_PATH)
    return parse(text)


# ---------------------------------------------------------------------------
# `--confirm-plan` interactive gate
# ---------------------------------------------------------------------------

ConfirmDecision = Literal["approve", "edit", "abort"]


def confirm_plan_interactively(
    plan_text: str,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    plan_host_path: Path | None = None,
) -> ConfirmDecision:
    """Render the plan and read a single approve/edit/abort decision.

    `plan_host_path` is the host path used by the `edit` branch when launching
    `$EDITOR`; if not provided, `edit` falls back to "press Enter when done".
    """
    out = stdout or sys.stdout
    inp = stdin or sys.stdin

    out.write("\n--- spek plan -------------------------------------------\n")
    out.write(plan_text)
    if not plan_text.endswith("\n"):
        out.write("\n")
    out.write("---------------------------------------------------------\n")
    out.write("Approve this plan? [approve / edit / abort]: ")
    out.flush()

    raw = inp.readline().strip().lower()
    if raw in {"a", "approve", "y", "yes"}:
        return "approve"
    if raw in {"e", "edit"}:
        if plan_host_path is not None and "EDITOR" in os.environ:
            try:
                subprocess.run([os.environ["EDITOR"], str(plan_host_path)], check=False)
            except FileNotFoundError:
                out.write(
                    f"\n$EDITOR not found; edit {plan_host_path} manually then press Enter.\n"
                )
                out.flush()
                inp.readline()
        else:
            out.write(
                f"\nNo $EDITOR set; edit {plan_host_path or PLAN_PATH} manually then press Enter.\n"
            )
            out.flush()
            inp.readline()
        return "edit"
    return "abort"
