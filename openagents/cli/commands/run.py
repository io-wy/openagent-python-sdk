"""``openagents run`` — execute an agent against a single prompt.

Input resolution order: ``--input TEXT`` > ``--input-file PATH`` >
non-TTY stdin > error. The command constructs a :class:`Runtime` via
:meth:`Runtime.from_config`, builds a :class:`RunRequest`, and drives
:meth:`Runtime.run_detailed`.

Output format:

* ``text`` (default when stdout is a TTY) — Rich-friendly transcript
  rendered via :class:`openagents.cli._events.EventFormatter`, with the
  final output printed on its own line.
* ``json`` — ``RunResult.model_dump(mode='json')``.
* ``events`` — one JSON line per event using
  :func:`openagents.cli._events.event_to_jsonl_dict` + a terminal
  ``run.finished`` event carrying the final output.

When stdout is not a TTY and ``--format`` wasn't passed explicitly, the
command defaults to ``events`` (JSONL) for pipe-friendliness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from openagents.cli._events import (
    EventFormatter,
    event_to_jsonl_dict,
)
from openagents.cli._exit import (
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    EXIT_VALIDATION,
)
from openagents.cli._rich import get_console
from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.runtime import RunRequest
from openagents.runtime.runtime import Runtime


def _resolve_input(args: argparse.Namespace) -> str | None:
    """Return the user's prompt, or ``None`` if nothing is available."""
    if args.input:
        return args.input
    if args.input_file:
        try:
            return Path(args.input_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"failed to read --input-file: {exc}", file=sys.stderr)
            return None
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data:
            return data
    return None


def _select_agent(cfg, requested: str | None) -> tuple[str | None, str | None]:
    """Return ``(agent_id, error_message)``."""
    if requested:
        for agent in cfg.agents:
            if agent.id == requested:
                return agent.id, None
        return None, f"agent not found: {requested}. Available: {[a.id for a in cfg.agents]}"
    if len(cfg.agents) == 1:
        return cfg.agents[0].id, None
    return None, (f"config declares {len(cfg.agents)} agents; pass --agent with one of: {[a.id for a in cfg.agents]}")


def _default_format(explicit: str | None) -> str:
    if explicit:
        return explicit
    return "text" if sys.stdout.isatty() else "events"


class _JsonlSubscriber:
    """Bridge that turns every event into a JSONL line on stdout."""

    def __init__(self) -> None:
        self._stream = sys.stdout

    def handle(self, event: Any) -> None:
        name = getattr(event, "name", None)
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            try:
                payload = dict(payload)
            except Exception:
                payload = {"raw": repr(payload)}
        self._stream.write(json.dumps(event_to_jsonl_dict(str(name), payload)) + "\n")
        self._stream.flush()


class _TextSubscriber:
    """Bridge that renders events via the shared EventFormatter."""

    def __init__(self) -> None:
        self._formatter = EventFormatter(get_console("stderr"), show_details=True)

    def handle(self, event: Any) -> None:
        name = getattr(event, "name", None)
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {"raw": repr(payload)}
        self._formatter.render(str(name), payload)


def _attach_subscriber(runtime: Runtime, handler: Any) -> None:
    """Subscribe *handler* to every interesting event on ``runtime.events``."""
    bus = getattr(runtime, "events", None)
    if bus is None:
        return
    for name in (
        "run.started",
        "run.finished",
        "tool.called",
        "tool.succeeded",
        "tool.failed",
        "llm.called",
        "llm.succeeded",
    ):
        try:
            bus.subscribe(name, handler)
        except Exception:  # pragma: no cover - defensive: subscribe shape varies
            pass


async def _run_once(
    runtime: Runtime,
    *,
    agent_id: str,
    session_id: str,
    input_text: str,
) -> Any:
    return await runtime.run_detailed(
        request=RunRequest(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
        )
    )


def _emit_final_output(result: Any, fmt: str) -> None:
    if fmt == "json":
        sys.stdout.write(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
        return
    if fmt == "events":
        sys.stdout.write(
            json.dumps(
                event_to_jsonl_dict(
                    "run.finished",
                    {
                        "run_id": result.run_id,
                        "stop_reason": str(result.stop_reason),
                        "final_output": str(result.final_output) if result.final_output is not None else None,
                        "error": result.error,
                    },
                )
            )
            + "\n"
        )
        return
    # text
    if result.final_output is not None:
        sys.stdout.write(str(result.final_output) + "\n")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "run",
        help="execute an agent against a single prompt",
        description="Run an agent.json once and print the transcript / final output.",
    )
    p.add_argument("path", help="path to an agent.json")
    p.add_argument("--input", help="prompt text (takes precedence over --input-file / stdin)")
    p.add_argument("--input-file", dest="input_file", help="path to a file containing the prompt")
    p.add_argument("--agent", dest="agent_id", help="agent id to run (required for multi-agent configs)")
    p.add_argument("--format", choices=["text", "json", "events"], default=None)
    p.add_argument("--no-stream", action="store_true", help="buffer events; print only the final output")
    p.add_argument(
        "--session-id",
        dest="session_id",
        default=None,
        help="reuse an explicit session id (default: auto-generated UUID)",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    agent_id, agent_err = _select_agent(cfg, args.agent_id)
    if agent_err is not None:
        print(agent_err, file=sys.stderr)
        return EXIT_USAGE

    prompt = _resolve_input(args)
    if prompt is None:
        print("no input provided. Pass --input, --input-file, or pipe text on stdin.", file=sys.stderr)
        return EXIT_USAGE

    fmt = _default_format(args.format)
    session_id = args.session_id or f"cli-{uuid.uuid4().hex[:8]}"

    try:
        runtime = Runtime.from_config(args.path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    subscriber: Any | None = None
    if not args.no_stream:
        if fmt == "events":
            subscriber = _JsonlSubscriber()
        elif fmt == "text":
            subscriber = _TextSubscriber()
    if subscriber is not None:
        _attach_subscriber(runtime, subscriber.handle)

    try:
        result = asyncio.run(
            _run_once(
                runtime,
                agent_id=agent_id or "",
                session_id=session_id,
                input_text=prompt,
            )
        )
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            asyncio.run(runtime.close())
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        return EXIT_RUNTIME

    _emit_final_output(result, fmt)
    try:
        asyncio.run(runtime.close())
    except Exception:  # pragma: no cover - best-effort cleanup
        pass
    if result.error:
        return EXIT_RUNTIME
    return EXIT_OK
