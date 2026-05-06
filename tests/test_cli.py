"""Tests for the CLI dispatcher and command handlers (no Docker, no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from spek.cli import _build_parser
from spek.commands import (
    EXIT_DOCKER_UNAVAILABLE,
    EXIT_NO_CREDENTIALS,
    EXIT_OK,
    EXIT_SPEC_INVALID,
    dispatch,
)
from spek.handlers.init_cmd import SPEC_TEMPLATE, run_init
from spek.spec import Spec, SpecValidationError


class _StubLLMClient:
    """Drop-in replacement for AnthropicLLMClient so CLI tests don't hit the network."""

    def __init__(self, model: str = "stub") -> None:
        self.model = model

    def create(self, **_: object) -> None:  # pragma: no cover - never called
        raise AssertionError("StubLLMClient.create should not be reached in CLI tests")


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace AnthropicLLMClient and provide a fake API key by default.

    The CLI tests never get far enough to actually call `.create()` — they
    exercise spec/docker error paths — but the build handler now requires
    ANTHROPIC_API_KEY to be set in the environment before constructing
    the client.
    """
    from spek.handlers import build_cmd as bc

    monkeypatch.setattr(bc.loop, "AnthropicLLMClient", _StubLLMClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Disable .env discovery so a real key on disk doesn't leak into tests.
    monkeypatch.setattr(bc, "_load_env_files", lambda _spec: None)


def test_parser_accepts_init() -> None:
    args = _build_parser().parse_args(["init", "/tmp/foo"])
    assert args.command == "init"
    assert args.directory == "/tmp/foo"


def test_parser_accepts_build_with_required_workdir() -> None:
    args = _build_parser().parse_args(
        ["build", "spec.md", "--workdir", "/tmp/out", "--confirm-plan"]
    )
    assert args.command == "build"
    assert args.spec == "spec.md"
    assert args.workdir == "/tmp/out"
    assert args.confirm_plan is True


def test_parser_rejects_build_without_workdir() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["build", "spec.md"])


def test_init_creates_template_and_spek_dir(tmp_path: Path) -> None:
    rc = run_init(str(tmp_path / "newproj"))

    assert rc == EXIT_OK
    assert (tmp_path / "newproj" / "SPEC.md").exists()
    assert (tmp_path / "newproj" / ".spek").is_dir()
    assert (tmp_path / "newproj" / "SPEC.md").read_text() == SPEC_TEMPLATE


def test_init_does_not_overwrite_existing_spec(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    target.mkdir()
    (target / "SPEC.md").write_text("# existing\n")

    run_init(str(target))

    assert (target / "SPEC.md").read_text() == "# existing\n"


def test_dispatch_init_invokes_handler(tmp_path: Path) -> None:
    args = _build_parser().parse_args(["init", str(tmp_path / "x")])
    rc = dispatch(args)
    assert rc == EXIT_OK
    assert (tmp_path / "x" / "SPEC.md").exists()


def test_build_returns_spec_invalid_for_missing_file(tmp_path: Path) -> None:
    args = _build_parser().parse_args(
        ["build", str(tmp_path / "nope.md"), "--workdir", str(tmp_path / "out")]
    )
    rc = dispatch(args)
    assert rc == EXIT_SPEC_INVALID


def test_build_returns_no_credentials_when_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Name\nx\n\n# Input\ny\n\n# Output\nz\n\n# Implementation\npython\n")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    args = _build_parser().parse_args(["build", str(spec), "--workdir", str(tmp_path / "out")])
    rc = dispatch(args)
    assert rc == EXIT_NO_CREDENTIALS


def test_build_returns_spec_invalid_when_llm_parser_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM-backed parser raises SpecValidationError; the CLI maps it to exit 2."""
    spec = tmp_path / "bad.md"
    spec.write_text("just text, no headers\n")

    from spek.handlers import build_cmd as bc

    def boom(*_a: object, **_kw: object) -> Spec:
        raise SpecValidationError("LLM said no")

    monkeypatch.setattr(bc, "parse_file", boom)

    args = _build_parser().parse_args(["build", str(spec), "--workdir", str(tmp_path / "out")])
    rc = dispatch(args)
    assert rc == EXIT_SPEC_INVALID


def test_build_returns_docker_unavailable_when_daemon_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Name\nx\n\n# Input\ny\n\n# Output\nz\n\n# Implementation\npython\n")

    # Don't actually call the LLM; pretend the spec parsed cleanly.
    from spek.handlers import build_cmd as bc

    def fake_parse_file(*_a: object, **_kw: object) -> Spec:
        return Spec(
            name="x",
            sections={"Name": "x", "Input": "y", "Output": "z", "Implementation": "python"},
            extras={},
        )

    monkeypatch.setattr(bc, "parse_file", fake_parse_file)

    # Force `docker info` to "fail": point the CLI at a non-existent binary.
    monkeypatch.setenv("PATH", "/nonexistent")

    # Make DockerSandbox find no `docker` binary regardless of system state.
    from spek.sandbox import docker as docker_mod

    monkeypatch.setattr(docker_mod.shutil, "which", lambda _name: None)

    args = _build_parser().parse_args(["build", str(spec), "--workdir", str(tmp_path / "out")])
    rc = dispatch(args)

    assert rc == EXIT_DOCKER_UNAVAILABLE
