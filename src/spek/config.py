"""`.spek/command_config.json`: the agent's view of how to operate the project.

Stored as JSON so the user can edit it between runs. The plan rule is
"agent writes silently; user-edits take precedence" — implemented here by
merging the on-disk file (if any) over the language defaults whenever
`load_or_create` is called.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spek.languages.base import LanguageDefaults, LanguageProfile


class CommandConfig(BaseModel):
    """Commands the agent uses to operate on the generated package."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(..., description="Language profile name, e.g. 'python'.")
    package_name: str = Field(..., description="Slug of the package being built.")
    build_command: str
    test_command: str
    lint_command: str
    format_command: str
    run_command: str

    @classmethod
    def from_defaults(cls, profile: LanguageProfile, package_name: str) -> CommandConfig:
        """Build a fresh config from a language profile's defaults."""
        d: LanguageDefaults = profile.default_commands(package_name)
        return cls(
            language=profile.name,
            package_name=package_name,
            build_command=d.build_command,
            test_command=d.test_command,
            lint_command=d.lint_command,
            format_command=d.format_command,
            run_command=d.run_command,
        )


def load_or_create(
    path: str | Path,
    *,
    profile: LanguageProfile,
    package_name: str,
) -> CommandConfig:
    """Load `path`, or seed it from `profile` defaults if it doesn't exist.

    User edits to the file always win: when the file exists we treat it as
    the source of truth and only fall back to defaults if a key is missing
    (which would be a hand-edit error worth surfacing).
    """
    p = Path(path)
    defaults = CommandConfig.from_defaults(profile, package_name)
    if not p.exists():
        save(defaults, p)
        return defaults

    raw = json.loads(p.read_text(encoding="utf-8"))
    # Merge defaults under user values; user wins.
    merged = {**defaults.model_dump(), **raw}
    cfg = CommandConfig.model_validate(merged)
    return cfg


def save(cfg: CommandConfig, path: str | Path) -> None:
    """Persist `cfg` to `path` (creating parents as needed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.model_dump_json(indent=2) + "\n", encoding="utf-8")
