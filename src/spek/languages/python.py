"""Python language profile: uv + pytest + ruff + black."""

from __future__ import annotations

import re

from spek.languages.base import LanguageDefaults, LanguageProfile


class PythonProfile(LanguageProfile):
    """Python target packages built with `uv`, tested with `pytest`."""

    name = "python"
    docker_image = "python:3.12-slim"
    # Bring the container to a usable state on first start. Idempotent: re-running
    # is a no-op once uv is on PATH.
    #
    # `pip install --user` writes into $HOME/.local, which the sandbox sets to
    # the chowned cache volume. `--user` works even though the container runs
    # as a non-root UID with no /etc/passwd entry, because pip honours $HOME
    # explicitly. The cache volume persists across spek runs so re-installing
    # uv on subsequent builds is a no-op.
    setup_cmd = [
        "bash",
        "-lc",
        "command -v uv >/dev/null 2>&1 || pip install --user --quiet --no-warn-script-location uv",
    ]

    def default_commands(self, package_name: str) -> LanguageDefaults:
        # `uv build` requires a [build-system] table; the agent is responsible
        # for producing one as part of step 1 of the plan. `uv run pytest`
        # implicitly creates the venv on first invocation.
        return LanguageDefaults(
            build_command="uv build",
            test_command="uv run pytest",
            lint_command="uv run ruff check .",
            format_command="uv run black --check .",
            run_command=f"uv run {package_name} --help",
        )

    _PYTEST_SUMMARY_RE = re.compile(
        r"^=+\s*(?:(?P<failed>\d+)\s+failed,\s*)?(?P<passed>\d+)\s+passed",
        re.MULTILINE,
    )
    _PYTEST_NO_TESTS_RE = re.compile(r"no tests ran", re.IGNORECASE)

    def parse_test_count(self, stdout: str) -> int:
        if self._PYTEST_NO_TESTS_RE.search(stdout):
            return 0
        # Walk all summary lines and take the last one (handles `--collect-only`
        # being followed by a real run).
        last = 0
        for m in self._PYTEST_SUMMARY_RE.finditer(stdout):
            last = int(m.group("passed"))
        return last
