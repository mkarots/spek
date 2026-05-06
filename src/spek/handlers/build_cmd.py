"""`spek build`: orchestrate the three-phase agent loop."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from spek.agent import loop
from spek.agent.reporter import ConsoleReporter, NullReporter
from spek.commands import (
    EXIT_BLOCKED,
    EXIT_CAP_HIT,
    EXIT_DOCKER_UNAVAILABLE,
    EXIT_NO_CREDENTIALS,
    EXIT_OK,
    EXIT_SPEC_INVALID,
)
from spek.config import load_or_create
from spek.journal import Journal
from spek.languages import select_profile
from spek.sandbox.docker import DockerSandbox, DockerUnavailableError
from spek.spec import SpecValidationError, parse_file


def _load_env_files(spec_src: Path) -> None:
    """Load `.env` from CWD, the spec's directory, and any ancestor.

    `load_dotenv()` with no args only checks CWD. When `spek` is installed
    system-wide and run from somewhere else, that misses the `.env` next
    to the spec or in the project root. We try CWD first (highest
    precedence — `load_dotenv` does not override existing vars), then walk
    up from the spec's directory.
    """
    load_dotenv()
    spec_dir = spec_src.resolve().parent if spec_src.exists() else Path.cwd()
    found = find_dotenv(filename=".env", usecwd=False, raise_error_if_not_found=False)
    if not found:
        # fall back to walking up from the spec's directory
        for candidate in [spec_dir, *spec_dir.parents]:
            env_path = candidate / ".env"
            if env_path.is_file():
                found = str(env_path)
                break
    if found:
        load_dotenv(found)


def _missing_credentials_message() -> str:
    return (
        "spek: Anthropic credentials are not set.\n"
        "  Set ANTHROPIC_API_KEY in your environment, or create a `.env`\n"
        "  file in the working directory or alongside the spec with:\n"
        "    ANTHROPIC_API_KEY=sk-...\n"
        "    ANTHROPIC_BASE_URL=https://...   # optional, for proxies"
    )


def _spek_dir(workdir: Path) -> Path:
    return workdir / ".spek"


def _wipe_spek_dir(workdir: Path) -> None:
    target = _spek_dir(workdir)
    if target.exists():
        shutil.rmtree(target)


def _copy_spec_into_workdir(spec_src: Path, workdir: Path) -> Path:
    target = _spek_dir(workdir) / "SPEC.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(spec_src.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def run_build(
    *,
    spec_path: str,
    workdir: str,
    fresh: bool,
    max_steps: int,
    max_seconds: int,
    confirm_plan: bool,
    quiet: bool = False,
) -> int:
    """Entrypoint for `spek build`. Returns the process exit code."""
    reporter = NullReporter() if quiet else ConsoleReporter()

    spec_src = Path(spec_path)
    if not spec_src.exists():
        print(f"spek: spec file not found: {spec_src}", file=sys.stderr)
        return EXIT_SPEC_INVALID

    # Pull ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY from a `.env` near the user
    # or near the spec. Done after the spec-existence check so we don't
    # walk the filesystem when the user mistyped the path.
    _load_env_files(spec_src)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(_missing_credentials_message(), file=sys.stderr)
        return EXIT_NO_CREDENTIALS

    model = os.environ.get("ANTHROPIC_MODEL", loop.DEFAULT_MODEL)
    client = loop.AnthropicLLMClient(model=model)

    reporter.start(spec_name=spec_src.name, workdir=str(Path(workdir).resolve()), model=model)

    try:
        spec = parse_file(spec_src, client=client, model=model)
    except SpecValidationError as exc:
        print(f"spek: invalid spec: {exc}", file=sys.stderr)
        return EXIT_SPEC_INVALID

    work = Path(workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)

    if fresh:
        _wipe_spek_dir(work)

    _copy_spec_into_workdir(spec_src, work)

    profile = select_profile(spec.implementation)
    cfg_path = _spek_dir(work) / "command_config.json"
    cfg = load_or_create(cfg_path, profile=profile, package_name=spec.name)

    journal = Journal(_spek_dir(work) / "journal.jsonl")

    try:
        with DockerSandbox(
            host_workdir=work,
            image=profile.docker_image,
            setup_cmd=profile.setup_cmd,
        ) as sandbox:
            result = loop.run(
                spec=spec,
                profile=profile,
                cfg=cfg,
                sandbox=sandbox,
                journal=journal,
                client=client,
                model=model,
                plan_host_path=_spek_dir(work) / "plan.md",
                confirm_plan=confirm_plan,
                max_steps=max_steps,
                max_seconds=max_seconds,
                reporter=reporter,
            )
    except DockerUnavailableError as exc:
        print(f"spek: Docker is required but not available:\n  {exc}", file=sys.stderr)
        print(
            "Hint: start Docker Desktop or `colima start`, then re-run `spek build`.",
            file=sys.stderr,
        )
        return EXIT_DOCKER_UNAVAILABLE

    if result.success:
        print(f"\nspek: success — {result.reason} (tests passed: {result.test_count})")
        return EXIT_OK

    print(f"\nspek: stopped — {result.reason}", file=sys.stderr)
    if "cap hit" in result.reason:
        return EXIT_CAP_HIT
    return EXIT_BLOCKED
