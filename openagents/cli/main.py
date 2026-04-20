"""Argparse-based CLI dispatcher for ``openagents``.

The dispatcher walks the ordered registry in
:mod:`openagents.cli.commands` and lazy-imports each subcommand module on
first use. Each command module exports ``add_parser(subparsers)`` to wire
its argparse tree (setting ``func=run`` via ``set_defaults``) and
``run(args)`` as the execution entry point.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Sequence

from openagents.cli.commands import COMMANDS, module_name_for


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openagents",
        description="OpenAgents SDK command-line utilities.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="print SDK, Python, and extras versions and exit",
    )
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        module = importlib.import_module(f"openagents.cli.commands.{module_name_for(name)}")
        module.add_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False) and not args.command:
        # Root -V / --version: dispatch into the version subcommand with
        # default flags so the output format stays consistent.
        from openagents.cli.commands import version as _version

        ns = argparse.Namespace(verbose=False, format="text")
        return _version.run(ns)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 1
    func = getattr(args, "func", None)
    if func is None:
        print(f"unknown subcommand: {args.command}", file=sys.stderr)
        return 1
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
