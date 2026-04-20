"""``openagents chat`` — interactive multi-turn REPL.

Holds a single :class:`Runtime` open across turns and reuses the same
``session_id`` so the session backend accumulates transcript across
prompts. Slash commands are handled locally (no LLM round-trip):

* ``/exit`` — clean exit (``0``).
* ``/reset`` — regenerate the session id so the next turn starts fresh.
* ``/save <path>`` — dump the current in-memory transcript to *path*
  as a JSON envelope compatible with ``openagents replay``.
* ``/context`` — print the last turn's final output.
* ``/tools`` — list tool ids + descriptions registered on the runtime.

Input uses :func:`questionary.text` when available and falls back to
:func:`input` otherwise so the command degrades cleanly without the
``cli`` extras.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from openagents.cli._exit import EXIT_OK, EXIT_USAGE, EXIT_VALIDATION
from openagents.cli._fallback import require_or_hint
from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.runtime import RunRequest
from openagents.runtime.runtime import Runtime


def _select_agent(cfg, requested: str | None) -> tuple[str | None, str | None]:
    if requested:
        for agent in cfg.agents:
            if agent.id == requested:
                return agent.id, None
        return None, f"agent not found: {requested}. Available: {[a.id for a in cfg.agents]}"
    if len(cfg.agents) == 1:
        return cfg.agents[0].id, None
    return None, (f"config declares {len(cfg.agents)} agents; pass --agent with one of: {[a.id for a in cfg.agents]}")


def _prompt(question_module: Any | None, prompt: str) -> str | None:
    if question_module is not None:
        try:
            text = question_module.text(prompt).ask()
        except Exception:
            return None
        return str(text) if text is not None else None
    try:
        return input(prompt)
    except EOFError:
        return None


def _list_tools(runtime: Runtime, agent_id: str) -> list[dict[str, str]]:
    """Return tool summaries for the given agent, best-effort.

    The runtime's tool registry can vary by implementation — we grab
    whatever attributes are available without crashing the REPL.
    """
    try:
        cfg = runtime.config  # type: ignore[attr-defined]
    except AttributeError:
        return []
    for agent in getattr(cfg, "agents", []):
        if getattr(agent, "id", None) != agent_id:
            continue
        rows: list[dict[str, str]] = []
        for tool in getattr(agent, "tools", []) or []:
            rows.append(
                {
                    "id": str(getattr(tool, "id", "") or ""),
                    "type": str(getattr(tool, "type", "") or ""),
                }
            )
        return rows
    return []


async def _run_one_turn(
    runtime: Runtime,
    *,
    agent_id: str,
    session_id: str,
    text: str,
) -> Any:
    return await runtime.run_detailed(
        request=RunRequest(
            agent_id=agent_id,
            session_id=session_id,
            input_text=text,
        )
    )


def _dispatch_slash(
    line: str,
    *,
    runtime: Runtime,
    agent_id: str,
    session_id: str,
    last_result: Any | None,
    console_out,
) -> tuple[bool, str]:
    """Return ``(should_exit, new_session_id)``.

    Slash commands are handled in-line; the caller passes the current
    session id and receives a (possibly rotated) one back so ``/reset``
    works transparently.
    """
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        return True, session_id
    if cmd == "/reset":
        new_sid = f"cli-chat-{uuid.uuid4().hex[:8]}"
        console_out.write(f"(session reset → {new_sid})\n")
        return False, new_sid
    if cmd == "/save":
        if not arg:
            console_out.write("usage: /save <path>\n")
            return False, session_id
        path = Path(arg)
        envelope = {
            "schema": 1,
            "session_id": session_id,
            "events": _last_result_as_events(last_result),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        console_out.write(f"(saved → {path})\n")
        return False, session_id
    if cmd == "/context":
        if last_result is None:
            console_out.write("(no previous turn yet)\n")
        else:
            console_out.write(f"final_output: {last_result.final_output}\nstop_reason : {last_result.stop_reason}\n")
        return False, session_id
    if cmd == "/tools":
        rows = _list_tools(runtime, agent_id)
        if not rows:
            console_out.write("(no tools registered for this agent)\n")
        else:
            for row in rows:
                console_out.write(f"  {row['id']:<20} ({row['type']})\n")
        return False, session_id
    console_out.write(f"unknown slash command: {cmd}. Try /exit, /reset, /save <path>, /context, /tools\n")
    return False, session_id


def _last_result_as_events(result: Any | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    return [
        {
            "name": "run.finished",
            "payload": {
                "run_id": getattr(result, "run_id", None),
                "stop_reason": str(getattr(result, "stop_reason", "")),
                "final_output": str(getattr(result, "final_output", "") or ""),
            },
        }
    ]


async def _chat_loop(
    runtime: Runtime,
    *,
    agent_id: str,
    session_id: str,
    question_module: Any | None,
    console_in,
    console_out,
) -> int:
    last_result: Any | None = None
    current_session = session_id
    while True:
        line = _prompt(question_module, "you> ")
        if line is None:
            console_out.write("\n")  # clean EOF
            return EXIT_OK
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/"):
            should_exit, current_session = _dispatch_slash(
                stripped,
                runtime=runtime,
                agent_id=agent_id,
                session_id=current_session,
                last_result=last_result,
                console_out=console_out,
            )
            if should_exit:
                return EXIT_OK
            continue
        try:
            result = await _run_one_turn(
                runtime,
                agent_id=agent_id,
                session_id=current_session,
                text=stripped,
            )
        except Exception as exc:
            console_out.write(f"[runtime error] {type(exc).__name__}: {exc}\n")
            continue
        last_result = result
        console_out.write(f"agent> {result.final_output}\n")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "chat",
        help="interactive multi-turn REPL",
        description="Start an interactive REPL against one agent from an agent.json.",
    )
    p.add_argument("path", help="path to an agent.json")
    p.add_argument("--agent", dest="agent_id", help="agent id (required for multi-agent configs)")
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

    agent_id, err = _select_agent(cfg, args.agent_id)
    if err is not None:
        print(err, file=sys.stderr)
        return EXIT_USAGE

    session_id = args.session_id or f"cli-chat-{uuid.uuid4().hex[:8]}"
    try:
        runtime = Runtime.from_config(args.path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    question_module = require_or_hint("questionary")
    console_out = sys.stdout
    console_in = sys.stdin

    print(
        f"openagents chat — agent={agent_id} session={session_id}\n"
        "type a message to send; /exit to quit, /help for slash commands.",
        file=sys.stderr,
    )
    try:
        code = asyncio.run(
            _chat_loop(
                runtime,
                agent_id=agent_id or "",
                session_id=session_id,
                question_module=question_module,
                console_in=console_in,
                console_out=console_out,
            )
        )
    except KeyboardInterrupt:
        console_out.write("\n(interrupted)\n")
        code = EXIT_OK
    finally:
        try:
            asyncio.run(runtime.close())
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    return code
