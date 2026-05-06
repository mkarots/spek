"""Language profile abstraction.

A `LanguageProfile` encapsulates everything spek needs to know to operate
inside a target language ecosystem: which Docker image to use, how to bring
that image up to a usable state, the default build/test/lint/format/run
commands, and how to interpret the test runner's stdout.

Adding a new language is a matter of subclassing `LanguageProfile` and
registering an instance in `spek.languages.__init__.REGISTRY`. No code
elsewhere in the package should switch on language.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageDefaults:
    """Default commands a `LanguageProfile` provides for a fresh project.

    Strings are passed verbatim to the bash tool; they may include shell
    metacharacters. They run inside the container at `/work`.
    """

    build_command: str
    test_command: str
    lint_command: str
    format_command: str
    run_command: str


class LanguageProfile(ABC):
    """Per-language metadata + behaviour.

    Concrete profiles are stateless and safe to share. Anything that varies
    per project (e.g. the package name) is passed as a method argument.
    """

    name: str
    docker_image: str
    setup_cmd: list[str]

    @abstractmethod
    def default_commands(self, package_name: str) -> LanguageDefaults:
        """Return the default build/test/lint/format/run commands."""

    @abstractmethod
    def parse_test_count(self, stdout: str) -> int:
        """Return the number of tests reported as passed in `stdout`.

        Returns 0 when the test runner ran but no tests were collected, or
        when the output is unrecognisable. The caller distinguishes "ran 0
        tests" from "ran" via the bash exit code.
        """
