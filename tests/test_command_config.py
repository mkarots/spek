"""Tests for spek.config (CommandConfig)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from spek.config import CommandConfig, load_or_create, save
from spek.languages.python import PythonProfile


@pytest.fixture
def profile() -> PythonProfile:
    return PythonProfile()


def test_from_defaults_seeds_all_fields_from_profile(profile: PythonProfile) -> None:
    cfg = CommandConfig.from_defaults(profile, "weather-tool")

    assert cfg.language == "python"
    assert cfg.package_name == "weather-tool"
    assert cfg.test_command == "uv run pytest"


def test_load_or_create_writes_file_when_missing(tmp_path: Path, profile: PythonProfile) -> None:
    target = tmp_path / "command_config.json"

    cfg = load_or_create(target, profile=profile, package_name="weather-tool")

    assert target.exists()
    on_disk = json.loads(target.read_text())
    assert on_disk["test_command"] == cfg.test_command


def test_load_or_create_returns_user_edits_unchanged(
    tmp_path: Path, profile: PythonProfile
) -> None:
    target = tmp_path / "command_config.json"
    user_value = "uv run pytest -k weather"
    save(
        CommandConfig.from_defaults(profile, "weather-tool").model_copy(
            update={"test_command": user_value}
        ),
        target,
    )

    cfg = load_or_create(target, profile=profile, package_name="weather-tool")

    assert cfg.test_command == user_value


def test_load_or_create_fills_missing_field_from_defaults(
    tmp_path: Path, profile: PythonProfile
) -> None:
    target = tmp_path / "command_config.json"
    # Hand-edit: drop a field. The agent should still be able to operate.
    incomplete = CommandConfig.from_defaults(profile, "weather-tool").model_dump()
    incomplete.pop("lint_command")
    target.write_text(json.dumps(incomplete), encoding="utf-8")

    cfg = load_or_create(target, profile=profile, package_name="weather-tool")

    assert cfg.lint_command.startswith("uv run ruff")


def test_extra_fields_are_rejected(tmp_path: Path, profile: PythonProfile) -> None:
    target = tmp_path / "command_config.json"
    raw = CommandConfig.from_defaults(profile, "weather-tool").model_dump()
    raw["deploy_command"] = "echo nope"
    target.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_or_create(target, profile=profile, package_name="weather-tool")


def test_save_creates_parent_directories(tmp_path: Path, profile: PythonProfile) -> None:
    nested = tmp_path / "a" / "b" / "command_config.json"
    cfg = CommandConfig.from_defaults(profile, "weather-tool")

    save(cfg, nested)

    assert nested.exists()
