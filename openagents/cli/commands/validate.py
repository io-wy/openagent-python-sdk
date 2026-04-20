"""``openagents validate`` — load an agent.json without running anything."""

from __future__ import annotations

import argparse
import json
import sys

from openagents.config.loader import load_config
from openagents.errors.exceptions import (
    ConfigError,
    ConfigLoadError,
    ConfigValidationError,
)
from openagents.plugins.registry import get_builtin_plugin_class


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "validate",
        help="validate an agent.json without running",
        description="Load an agent.json through the full config pipeline and report the result.",
    )
    p.add_argument("path", help="path to an agent.json")
    p.add_argument(
        "--strict",
        action="store_true",
        help="additionally verify every plugin 'type' resolves to a registered plugin",
    )
    p.add_argument(
        "--show-resolved",
        action="store_true",
        help="after validation, print the fully-resolved AppConfig",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.path)
    except ConfigLoadError as exc:
        print(f"ConfigLoadError: {exc}", file=sys.stderr)
        return 2
    except ConfigValidationError as exc:
        print(f"ConfigValidationError: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:  # pragma: no cover - generic fallback
        print(f"ConfigError: {exc}", file=sys.stderr)
        return 2

    if args.strict:
        unresolved: list[str] = []
        for agent in cfg.agents:
            for seam_name in (
                "memory",
                "pattern",
                "tool_executor",
                "context_assembler",
            ):
                ref = getattr(agent, seam_name, None)
                if ref is not None and ref.type and not ref.impl:
                    cls = get_builtin_plugin_class(seam_name, ref.type)
                    if cls is None:
                        unresolved.append(f"{agent.id}.{seam_name}={ref.type}")
        if unresolved:
            print(
                "unresolved plugin types (strict mode):",
                file=sys.stderr,
            )
            for u in unresolved:
                print(f"  - {u}", file=sys.stderr)
            return 2

    seams_configured = 0
    for agent in cfg.agents:
        for seam_name in (
            "memory",
            "pattern",
            "tool_executor",
            "context_assembler",
        ):
            if getattr(agent, seam_name, None) is not None:
                seams_configured += 1

    print(
        f"OK: {args.path} is valid ({len(cfg.agents)} agents, {seams_configured} seams configured)",
    )
    if args.show_resolved:
        print(json.dumps(cfg.model_dump(mode="json"), indent=2))
    return 0
