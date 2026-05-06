"""Tests for spek.agent.planning."""

from __future__ import annotations

import io

import pytest

from spek.agent import planning

PLAN_OK = """# Plan

1. [ ] Set up package skeleton
   - pyproject.toml with [build-system]
   - src/foo/__init__.py
2. [x] Add CLI entrypoint
3. [ ] Run build and tests and confirm green
"""


def test_parse_round_trip_preserves_steps_and_done_state() -> None:
    steps = planning.parse(PLAN_OK)

    assert [s.index for s in steps] == [1, 2, 3]
    assert [s.done for s in steps] == [False, True, False]
    assert "package skeleton" in steps[0].title
    assert "pyproject.toml" in (steps[0].details or "")


def test_parse_rejects_non_monotonic_numbering() -> None:
    text = "# Plan\n\n1. [ ] a\n3. [ ] b\n"
    with pytest.raises(planning.PlanParseError, match="non-monotonic"):
        planning.parse(text)


def test_parse_rejects_missing_checkbox() -> None:
    text = "# Plan\n\n1. just a step\n"
    with pytest.raises(planning.PlanParseError, match="checkbox"):
        planning.parse(text)


def test_parse_rejects_empty_plan() -> None:
    with pytest.raises(planning.PlanParseError, match="at least one"):
        planning.parse("# Plan\n\nno numbered items here\n")


def test_render_round_trips() -> None:
    steps = planning.parse(PLAN_OK)
    rendered = planning.render(steps)
    re_parsed = planning.parse(rendered)
    assert [(s.index, s.title, s.done) for s in steps] == [
        (s.index, s.title, s.done) for s in re_parsed
    ]


def test_mark_done_flips_only_target_step() -> None:
    steps = planning.parse(PLAN_OK)
    after = planning.mark_done(steps, 1)
    assert after[0].done is True
    assert after[1].done is True  # was already done
    assert after[2].done is False


def test_first_open_step_returns_lowest_unchecked() -> None:
    steps = planning.parse(PLAN_OK)
    assert planning.first_open_step(steps).index == 1
    after = planning.mark_done(steps, 1)
    assert planning.first_open_step(after).index == 3


def test_confirm_plan_approve_returns_approve() -> None:
    out = io.StringIO()
    in_ = io.StringIO("approve\n")
    decision = planning.confirm_plan_interactively(
        PLAN_OK, stdin=in_, stdout=out, plan_host_path=None
    )
    assert decision == "approve"


def test_confirm_plan_abort_returns_abort() -> None:
    out = io.StringIO()
    in_ = io.StringIO("abort\n")
    decision = planning.confirm_plan_interactively(
        PLAN_OK, stdin=in_, stdout=out, plan_host_path=None
    )
    assert decision == "abort"


def test_confirm_plan_unknown_input_treated_as_abort() -> None:
    out = io.StringIO()
    in_ = io.StringIO("\n")  # empty line
    decision = planning.confirm_plan_interactively(
        PLAN_OK, stdin=in_, stdout=out, plan_host_path=None
    )
    assert decision == "abort"


def test_confirm_plan_renders_plan_to_stdout() -> None:
    out = io.StringIO()
    in_ = io.StringIO("approve\n")
    planning.confirm_plan_interactively(PLAN_OK, stdin=in_, stdout=out, plan_host_path=None)
    rendered = out.getvalue()
    assert "1. [ ] Set up package skeleton" in rendered
    assert "Approve this plan?" in rendered
