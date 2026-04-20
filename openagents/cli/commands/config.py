"""``openagents config show`` — print the fully-resolved AppConfig.

Uses the same pipeline as ``openagents validate`` (env-var substitution
is applied by :func:`openagents.config.loader.load_config`) then expands
each ``type:`` reference to the concrete Python dotted path resolved by
:func:`openagents.plugins.registry.get_builtin_plugin_class` or the
decorator registries. The output is therefore a faithful view of "what
will actually run" rather than "what the file says".

``--redact`` walks the output tree and replaces any leaf whose JSON path
contains ``api_key``, ``token``, ``password``, or ``secret`` (case-
insensitive) with the literal string ``***`` so the command is safe to
pipe into bug reports.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from typing import Any

from openagents.cli._exit import EXIT_OK, EXIT_VALIDATION
from openagents.cli._fallback import require_or_hint
from openagents.config.loader import load_config
from openagents.decorators import (
    _CONTEXT_ASSEMBLER_REGISTRY,
    _EVENT_REGISTRY,
    _MEMORY_REGISTRY,
    _PATTERN_REGISTRY,
    _RUNTIME_REGISTRY,
    _SESSION_REGISTRY,
    _TOOL_EXECUTOR_REGISTRY,
    _TOOL_REGISTRY,
)
from openagents.errors.exceptions import ConfigError
from openagents.plugins.registry import get_builtin_plugin_class

_DECORATOR_REGISTRIES: dict[str, dict[str, Any]] = {
    "memory": _MEMORY_REGISTRY,
    "pattern": _PATTERN_REGISTRY,
    "runtime": _RUNTIME_REGISTRY,
    "session": _SESSION_REGISTRY,
    "events": _EVENT_REGISTRY,
    "tool_executor": _TOOL_EXECUTOR_REGISTRY,
    "context_assembler": _CONTEXT_ASSEMBLER_REGISTRY,
    "tool": _TOOL_REGISTRY,
}

_REDACT_PATTERN = re.compile(r"api[_-]?key|token|password|secret", re.IGNORECASE)

_SEAM_KEYS = {
    "memory",
    "pattern",
    "tool_executor",
    "context_assembler",
    "followup_resolver",
    "response_repair_policy",
    "execution_policy",
    "runtime",
    "session",
    "events",
}


def _resolve_impl(seam: str, type_name: str) -> str | None:
    """Return ``module.Class`` for a plugin referenced by ``type``."""
    cls = get_builtin_plugin_class(seam, type_name)
    if cls is None:
        cls = _DECORATOR_REGISTRIES.get(seam, {}).get(type_name)
    if cls is None:
        return None
    return f"{getattr(cls, '__module__', '?')}.{getattr(cls, '__name__', str(cls))}"


def _annotate_refs(obj: Any, seam_key: str | None = None) -> Any:
    """Recursively annotate dicts shaped like plugin refs with ``impl:<path>``.

    A plugin ref is a dict that contains a ``type`` key and lives under a
    seam key such as ``memory`` / ``pattern`` / ``tool_executor``. The
    ``tools`` array contains a list of tool refs.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        looks_like_ref = seam_key is not None and "type" in obj and obj.get("impl") is None
        for k, v in obj.items():
            nested_seam = k if k in _SEAM_KEYS else ("tool" if k == "tools" else None)
            out[k] = _annotate_refs(v, nested_seam)
        if looks_like_ref:
            resolved = _resolve_impl(seam_key, obj["type"])
            if resolved is not None:
                # pydantic dumps ``impl`` as an explicit ``None`` key; overwrite
                # so the resolved path is visible in the output.
                if out.get("impl") is None:
                    out["impl"] = resolved
        return out
    if isinstance(obj, list):
        return [_annotate_refs(item, seam_key) for item in obj]
    return obj


def _redact(obj: Any, *, path: tuple[str, ...] = ()) -> Any:
    """Replace leaves whose path segments match the secret pattern with ``***``."""
    if isinstance(obj, dict):
        return {k: _redact(v, path=path + (str(k),)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(item, path=path) for item in obj]
    for segment in path:
        if _REDACT_PATTERN.search(segment):
            return "***"
    return obj


def _dump(data: Any, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    yaml = require_or_hint("yaml")
    if yaml is None:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "config",
        help="inspect configuration",
        description="Inspect an agent.json after resolution.",
    )
    nested = p.add_subparsers(dest="config_action")
    show = nested.add_parser("show", help="print the fully-resolved AppConfig")
    show.add_argument("path", help="path to an agent.json")
    show.add_argument("--format", choices=["json", "yaml"], default="json")
    show.add_argument(
        "--redact",
        action="store_true",
        help="replace api_key/token/password/secret fields with ***",
    )
    show.set_defaults(func=run)
    # Parent command without a sub-action just prints help and returns 1.
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    action = getattr(args, "config_action", None)
    if action is None:
        # ``openagents config`` without a sub-action.
        print("usage: openagents config show <path> [--format json|yaml] [--redact]", file=sys.stderr)
        return 1
    if action != "show":  # pragma: no cover - guarded by argparse choices
        print(f"unknown config action: {action}", file=sys.stderr)
        return 1
    try:
        cfg = load_config(args.path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    data = cfg.model_dump(mode="json")
    data = _annotate_refs(copy.deepcopy(data))
    if args.redact:
        data = _redact(data)
    sys.stdout.write(_dump(data, args.format) + ("\n" if args.format == "json" else ""))
    return EXIT_OK
