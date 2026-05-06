"""Command dispatch for the CLI.

Kept separate from `cli.py` so the argparse wiring is trivial and the
command handlers can be unit-tested without invoking argparse.
"""

from __future__ import annotations

import argparse

EXIT_OK = 0
EXIT_DOCKER_UNAVAILABLE = 2
EXIT_SPEC_INVALID = 3
EXIT_CAP_HIT = 4
EXIT_BLOCKED = 5
EXIT_NO_CREDENTIALS = 6


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch a parsed CLI namespace to the right command handler."""
    if args.command == "init":
        from spek.handlers.init_cmd import run_init

        return run_init(args.directory)
    if args.command == "build":
        from spek.handlers.build_cmd import run_build

        return run_build(
            spec_path=args.spec,
            workdir=args.workdir,
            fresh=args.fresh,
            max_steps=args.max_steps,
            max_seconds=args.max_seconds,
            confirm_plan=args.confirm_plan,
            quiet=getattr(args, "quiet", False),
        )
    raise SystemExit(f"unknown command: {args.command!r}")
