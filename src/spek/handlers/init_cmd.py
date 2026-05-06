"""`spek init`: scaffold an empty SPEC.md + .spek/ in a directory."""

from __future__ import annotations

from pathlib import Path

from spek.commands import EXIT_OK

SPEC_TEMPLATE = """# Name
<package-name>

A free-form description of what this package does.

# Input
- arg1, str, description
- arg2, int, default=0, description

# Output
Describe what the program emits and in what format.

# Implementation
Language: python
Frameworks: ...
"""


def run_init(directory: str) -> int:
    """Create `<directory>/SPEC.md` and `<directory>/.spek/` (idempotent)."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".spek").mkdir(exist_ok=True)
    spec_path = target / "SPEC.md"
    if not spec_path.exists():
        spec_path.write_text(SPEC_TEMPLATE, encoding="utf-8")
        print(f"wrote {spec_path}")
    else:
        print(f"{spec_path} already exists, leaving it untouched")
    return EXIT_OK
