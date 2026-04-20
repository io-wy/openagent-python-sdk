"""``openagents doctor`` — one-shot environment health check.

Reports Python version against the minimum declared in the distribution
metadata, detection of optional extras, and presence (not value!) of
well-known provider API-key environment variables. Exit code is ``0`` iff
every *required* check passes; everything else renders as a warning.

With ``--config PATH`` the command also runs the given ``agent.json``
through :func:`openagents.config.loader.load_config` and reports any
validation error.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
from typing import Any

from openagents.cli._exit import EXIT_OK, EXIT_USAGE
from openagents.plugins.registry import _BUILTIN_REGISTRY

_DIST_NAME = "io-openagent-sdk"

_OPTIONAL_EXTRAS = (
    "rich",
    "questionary",
    "yaml",
    "watchdog",
    "anthropic",
    "mcp",
    "mem0ai",
)

_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
)


def _python_meets_minimum() -> tuple[bool, str, str]:
    """Return ``(ok, detected, required)`` comparing against the distribution."""
    detected = ".".join(str(n) for n in sys.version_info[:3])
    try:
        required_raw = importlib.metadata.metadata(_DIST_NAME).get("Requires-Python") or ""
    except importlib.metadata.PackageNotFoundError:
        return True, detected, "(distribution not installed)"
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", required_raw)
    if not match:
        return True, detected, required_raw or "(unparseable)"
    required_tuple = tuple(int(p) if p else 0 for p in match.groups())
    return (
        sys.version_info[:3] >= required_tuple,
        detected,
        ".".join(str(n) for n in required_tuple[: len(match.groups())] if n is not None),
    )


def _extras_status() -> list[dict[str, Any]]:
    return [{"name": name, "installed": importlib.util.find_spec(name) is not None} for name in _OPTIONAL_EXTRAS]


def _env_var_status() -> list[dict[str, Any]]:
    # NOTE: only report presence — never the value.
    return [{"name": n, "set": bool(os.environ.get(n))} for n in _PROVIDER_ENV_VARS]


def _builtin_plugin_counts() -> dict[str, int]:
    return {seam: len(plugins) for seam, plugins in _BUILTIN_REGISTRY.items()}


def _check_config(path: str) -> tuple[bool, str]:
    from openagents.config.loader import load_config
    from openagents.errors.exceptions import ConfigError

    try:
        cfg = load_config(path)
    except ConfigError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"{len(cfg.agents)} agent(s) loaded"


def _build_report(config_path: str | None) -> dict[str, Any]:
    py_ok, py_detected, py_required = _python_meets_minimum()
    report: dict[str, Any] = {
        "python": {"ok": py_ok, "detected": py_detected, "required": py_required},
        "extras": _extras_status(),
        "env_vars": _env_var_status(),
        "builtin_plugin_counts": _builtin_plugin_counts(),
    }
    if config_path is not None:
        ok, detail = _check_config(config_path)
        report["config"] = {"path": config_path, "ok": ok, "detail": detail}
    # Required checks: Python version + at least one builtin seam has plugins
    # (if the package is installed correctly it always will).
    report["ok"] = py_ok and sum(report["builtin_plugin_counts"].values()) > 0
    if config_path is not None:
        report["ok"] = report["ok"] and report["config"]["ok"]
    return report


def _render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    py = report["python"]
    tag = "OK" if py["ok"] else "FAIL"
    lines.append(f"[{tag}]  Python {py['detected']} (required: {py['required']})")
    lines.append("Optional extras:")
    for row in report["extras"]:
        mark = "✓" if row["installed"] else "·"
        lines.append(f"  {mark} {row['name']}")
    lines.append("Provider env vars:")
    for row in report["env_vars"]:
        mark = "✓" if row["set"] else "·"
        lines.append(f"  {mark} {row['name']} {'set' if row['set'] else 'not set'}")
    counts = report["builtin_plugin_counts"]
    total = sum(counts.values())
    lines.append(f"Builtin plugins: {total} across {len(counts)} seams")
    if "config" in report:
        cfg = report["config"]
        mark = "OK" if cfg["ok"] else "FAIL"
        lines.append(f"[{mark}]  config {cfg['path']}: {cfg['detail']}")
    lines.append("")
    lines.append("Overall: " + ("OK" if report["ok"] else "FAIL"))
    return "\n".join(lines)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "doctor",
        help="diagnose environment health",
        description="Check Python version, extras, env vars, plugins, and (optionally) a config file.",
    )
    p.add_argument("--config", help="also run load_config(path) and report result")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    report = _build_report(args.config)
    if args.format == "json":
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        sys.stdout.write(_render_text(report) + "\n")
    return EXIT_OK if report["ok"] else EXIT_USAGE
