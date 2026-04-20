"""``openagents replay`` — re-render a persisted session transcript.

Accepts either a JSONL event stream (as produced by the future
``openagents run --format events``) or a JSON document holding a list of
events / a ``{"events": [...]}`` envelope. Events are fed through the
same :class:`openagents.cli._events.EventFormatter` used by ``run`` so
the rendering is identical.

``--turn N`` slices the event list to a single turn using
:func:`openagents.cli._events.iter_turns` (1-indexed on the natural turn
boundary — each ``run.started`` event begins a new turn).

``--format json`` re-emits the normalised event list so downstream
tooling can pipe the file through ``replay`` as a pretty-printer
without losing data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from openagents.cli._events import (
    EVENT_SCHEMA_VERSION,
    EventFormatter,
    iter_turns,
)
from openagents.cli._exit import EXIT_OK, EXIT_USAGE, EXIT_VALIDATION
from openagents.cli._rich import get_console


def _parse_input(text: str) -> list[dict[str, Any]]:
    """Parse *text* into a flat event list.

    Accepts (in order):

    1. JSONL — one JSON object per non-empty line.
    2. A JSON array of event objects.
    3. A JSON object with an ``events`` key.
    4. A SessionArtifact-shaped object with entries under ``transcript``
       / ``data`` — rendered as generic events.
    """
    stripped = text.strip()
    if not stripped:
        return []
    # Heuristic: JSONL if first non-empty line ends mid-text and there's
    # a second line. JSONL docs don't begin with ``[``.
    if not stripped.startswith("["):
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        if len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines):
            return [_coerce_event(json.loads(ln)) for ln in lines]
    data = json.loads(stripped)
    if isinstance(data, list):
        return [_coerce_event(item) for item in data]
    if isinstance(data, dict):
        if "events" in data and isinstance(data["events"], list):
            return [_coerce_event(item) for item in data["events"]]
        if "transcript" in data and isinstance(data["transcript"], list):
            return [_session_artifact_to_event(item) for item in data["transcript"]]
    raise ValueError(
        "unrecognised replay file shape — expected JSONL, a JSON array, or a dict with 'events' / 'transcript'"
    )


def _coerce_event(obj: Any) -> dict[str, Any]:
    """Normalize any supported shape into ``{name, payload}``."""
    if not isinstance(obj, dict):
        return {"name": "unknown", "payload": {"raw": obj}}
    # Shape from event_to_jsonl_dict: {"schema", "name", "payload"}.
    if "name" in obj:
        return {
            "name": str(obj["name"]),
            "payload": dict(obj.get("payload") or {}),
        }
    # Shape from a SessionArtifact inner record: {"type": "transcript", "data": ...}.
    if "type" in obj and "data" in obj:
        return {"name": f"artifact.{obj['type']}", "payload": {"data": obj["data"]}}
    return {"name": "unknown", "payload": dict(obj)}


def _session_artifact_to_event(entry: dict[str, Any]) -> dict[str, Any]:
    if isinstance(entry, dict) and "type" in entry:
        return {"name": f"artifact.{entry['type']}", "payload": {"data": entry.get("data")}}
    return {"name": "artifact.entry", "payload": {"data": entry}}


def _select_turn(events: list[dict[str, Any]], turn: int | None) -> list[dict[str, Any]]:
    if turn is None:
        return events
    if turn < 1:
        return []
    turns = list(iter_turns(events))
    if turn > len(turns):
        return []
    return turns[turn - 1]


def _emit_text(events: Iterable[dict[str, Any]]) -> None:
    console = get_console("stdout")
    formatter = EventFormatter(console, show_details=True)
    for ev in events:
        formatter.render(ev.get("name", "unknown"), ev.get("payload") or {})


def _emit_json(events: list[dict[str, Any]]) -> None:
    payload = {
        "schema": EVENT_SCHEMA_VERSION,
        "events": events,
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "replay",
        help="render a persisted session transcript",
        description="Re-render a session transcript (JSONL events or SessionArtifact JSON).",
    )
    p.add_argument("path", help="path to the JSONL event file or SessionArtifact JSON")
    p.add_argument(
        "--turn",
        type=int,
        default=None,
        help="render a single turn only (1-indexed; turn boundary is each 'run.started' event)",
    )
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"failed to read {path}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        events = _parse_input(text)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"failed to parse {path}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    selected = _select_turn(events, args.turn)
    if args.format == "json":
        _emit_json(selected)
    else:
        _emit_text(selected)
    return EXIT_OK
