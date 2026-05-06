"""Tests for the Python language profile and the language registry."""

from __future__ import annotations

import pytest

from spek.languages import (
    UnsupportedLanguageError,
    get,
    select_profile,
)
from spek.languages.python import PythonProfile


@pytest.fixture
def profile() -> PythonProfile:
    return PythonProfile()


def test_default_commands_uses_uv_pytest_ruff_black(profile: PythonProfile) -> None:
    cmds = profile.default_commands("weather-tool")

    assert cmds.build_command == "uv build"
    assert cmds.test_command == "uv run pytest"
    assert cmds.lint_command.startswith("uv run ruff")
    assert cmds.format_command.startswith("uv run black")
    assert "weather-tool" in cmds.run_command


def test_parse_test_count_one_passed(profile: PythonProfile) -> None:
    out = "==== 1 passed in 0.12s ====\n"
    assert profile.parse_test_count(out) == 1


def test_parse_test_count_many_passed(profile: PythonProfile) -> None:
    out = (
        "test_a.py .....                                                       [100%]\n"
        "==== 5 passed in 0.40s ====\n"
    )
    assert profile.parse_test_count(out) == 5


def test_parse_test_count_with_failures(profile: PythonProfile) -> None:
    # `2 failed, 3 passed` — we report the passed count.
    out = "==== 2 failed, 3 passed in 0.40s ====\n"
    assert profile.parse_test_count(out) == 3


def test_parse_test_count_no_tests_ran(profile: PythonProfile) -> None:
    out = "==== no tests ran in 0.01s ====\n"
    assert profile.parse_test_count(out) == 0


def test_parse_test_count_unrecognised_output(profile: PythonProfile) -> None:
    assert profile.parse_test_count("collected 0 items\n") == 0


def test_parse_test_count_takes_last_summary_line(profile: PythonProfile) -> None:
    # Two summaries (e.g. collect+run) - we take the last.
    out = "==== 1 passed in 0.01s ====\n==== 4 passed in 0.20s ====\n"
    assert profile.parse_test_count(out) == 4


def test_get_python_returns_singleton_profile() -> None:
    p1 = get("python")
    p2 = get("PYTHON")
    assert p1 is p2
    assert p1.name == "python"


def test_get_unknown_language_raises() -> None:
    with pytest.raises(UnsupportedLanguageError, match="not supported"):
        get("haskell")


def test_select_profile_finds_python_in_implementation_text() -> None:
    profile = select_profile("Use Python with click for the CLI.")
    assert profile.name == "python"


def test_select_profile_defaults_to_python_when_no_keyword_present() -> None:
    profile = select_profile("Just make it work.")
    assert profile.name == "python"
