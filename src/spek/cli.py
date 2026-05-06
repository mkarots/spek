"""Spek CLI entrypoint.

Wired fully in the `cli_wire` todo. This file exposes `main()` so
`uv run spek --help` works as soon as the package is installed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spek",
        description="Agentic spec-to-code builder.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Scaffold an empty SPEC.md and .spek/.")
    init_p.add_argument("directory", help="Directory to initialise.")

    build_p = sub.add_parser("build", help="Run the agent against a SPEC.md spec.")
    build_p.add_argument("spec", help="Path to SPEC.md.")
    build_p.add_argument("--workdir", required=True, help="Working directory for the package.")
    build_p.add_argument("--fresh", action="store_true", help="Wipe .spek/ before starting.")
    build_p.add_argument("--max-steps", type=int, default=120, help="Max tool calls per run.")
    build_p.add_argument(
        "--max-seconds", type=int, default=30 * 60, help="Wall-clock cap (seconds)."
    )
    build_p.add_argument(
        "--confirm-plan",
        action="store_true",
        help="Pause after phase 2 and require user approval of .spek/plan.md.",
    )
    build_p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output. The journal still records every event.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    from spek.commands import dispatch

    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
