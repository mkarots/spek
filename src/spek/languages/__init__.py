"""Registry of available language profiles.

Add a new profile by importing it and inserting an instance into `REGISTRY`.
Selection from a spec is done by `select_profile(spec_implementation_text)`,
which scans for a language keyword. Unknown languages fail loudly so we
never silently downgrade to "python" and produce nonsense.
"""

from __future__ import annotations

from spek.languages.base import LanguageDefaults, LanguageProfile
from spek.languages.python import PythonProfile


class UnsupportedLanguageError(ValueError):
    """Raised when the spec asks for a language spek v1 does not support."""


REGISTRY: dict[str, LanguageProfile] = {
    "python": PythonProfile(),
}


def get(name: str) -> LanguageProfile:
    """Return the registered profile for `name` (case-insensitive)."""
    try:
        return REGISTRY[name.lower()]
    except KeyError as exc:
        supported = ", ".join(sorted(REGISTRY))
        raise UnsupportedLanguageError(
            f"language {name!r} is not supported by spek v1 (supported: {supported})"
        ) from exc


# Order matters: the first match wins. Python aliases keep the obvious
# variants together so a spec saying "py3" still resolves.
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("python", "python"),
    ("py3", "python"),
)


def select_profile(implementation_text: str) -> LanguageProfile:
    """Choose a profile by scanning the spec's `# Implementation` section.

    Defaults to Python if no known keyword is found. The plan's "v1 only
    supports Python" rule is enforced by `get()` raising on unknowns elsewhere.
    """
    haystack = implementation_text.lower()
    for keyword, profile_name in _KEYWORDS:
        if keyword in haystack:
            return get(profile_name)
    return get("python")


__all__ = [
    "LanguageDefaults",
    "LanguageProfile",
    "PythonProfile",
    "REGISTRY",
    "UnsupportedLanguageError",
    "get",
    "select_profile",
]
