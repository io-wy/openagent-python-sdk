"""CLI wrapper for the local CoreCoder example runner.

Usage::

    uv run python examples/corecoder_agent/run_demo.py
    uv run python examples/corecoder_agent/run_demo.py --interactive
    uv run python examples/corecoder_agent/run_demo.py --input "read README.md"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.corecoder_agent.app.runner import CoreCoderLocalRunner


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_env_aliases() -> None:
    """Map common OpenAI-style env names into the example's LLM_* variables."""
    alias_pairs = (
        ("OPENAI_API_KEY", "LLM_API_KEY"),
        ("OPENAI_BASE_URL", "LLM_API_BASE"),
        ("OPENAI_MODEL", "LLM_MODEL"),
    )
    for src, dst in alias_pairs:
        if os.environ.get(src) and not os.environ.get(dst):
            os.environ[dst] = os.environ[src]

    provider = os.environ.get("LLM_PROVIDER")
    if not provider:
        api_base = (os.environ.get("LLM_API_BASE") or "").lower()
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
            os.environ["LLM_PROVIDER"] = "openai_compatible"
        elif api_base and "anthropic" not in api_base:
            os.environ["LLM_PROVIDER"] = "openai_compatible"
        else:
            os.environ["LLM_PROVIDER"] = "anthropic"

    if (
        os.environ.get("LLM_PROVIDER") == "openai_compatible"
        and not os.environ.get("LLM_API_BASE")
    ):
        os.environ["LLM_API_BASE"] = "https://api.openai.com/v1"


def render_task_brief(workspace: Path) -> str:
    task_md = workspace / "TASK.md"
    body = task_md.read_text(encoding="utf-8") if task_md.exists() else ""
    return (
        "There are bugs in workspace/stats.py that make workspace/test_stats.py "
        "fail. Find and fix them with edit_file (one minimal edit per bug), then "
        "verify by running the test suite. The full brief is below.\n\n"
        f"---\n{body}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CoreCoder example locally.")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="start a multi-turn REPL instead of the bundled bug-fix demo",
    )
    parser.add_argument(
        "--input",
        help="run one prompt and exit",
    )
    parser.add_argument(
        "--agent-id",
        default="corecoder",
        help="agent id from agent.json (default: corecoder)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="reuse an explicit session id (default: auto-generated per mode)",
    )
    return parser.parse_args(argv)


def _print_usage(result: Any) -> None:
    print("=" * 70)
    print("USAGE:")
    print("=" * 70)
    if result.usage is not None:
        print(
            f"  llm_calls={result.usage.llm_calls}  "
            f"tool_calls={result.usage.tool_calls}  "
            f"input={result.usage.input_tokens}  "
            f"output={result.usage.output_tokens}  "
            f"cost_usd={result.usage.cost_usd}"
        )


def _print_final(result: Any) -> None:
    print("=" * 70)
    print("FINAL ANSWER:")
    print("=" * 70)
    if result.stop_reason.value == "failed" and result.error_details is not None:
        print(f"[ERROR] {result.error_details.message}")
    else:
        print(result.final_output)
    print()
    _print_usage(result)


async def _run_demo_mode(
    runner: CoreCoderLocalRunner,
    *,
    root: Path,
    agent_id: str,
    session_id: str,
) -> None:
    print("[INFO] Running CoreCoder example agent")
    print("[INFO] Workspace:", root / "workspace")
    print()

    brief = render_task_brief(root / "workspace")
    result = await runner.run_detailed(
        agent_id=agent_id,
        session_id=session_id,
        input_text=brief,
    )
    _print_final(result)
    print()
    print("[INFO] Verify the fix yourself:")
    print(f"       cd {root / 'workspace'}")
    print("       python -m unittest test_stats.py")


async def _run_one_shot(
    runner: CoreCoderLocalRunner,
    *,
    agent_id: str,
    session_id: str,
    text: str,
) -> None:
    result = await runner.run_detailed(
        agent_id=agent_id,
        session_id=session_id,
        input_text=text,
    )
    _print_final(result)


def _print_repl_help() -> None:
    print("/help                show this help")
    print("/exit or /quit       exit")
    print("/reset               start a fresh session id")
    print("/session             print the current session id")
    print("/usage               print the last turn's usage counters")


async def _run_interactive(
    runner: CoreCoderLocalRunner,
    *,
    agent_id: str,
    session_id: str,
) -> None:
    current_session = session_id
    last_result: Any | None = None
    print(f"[INFO] CoreCoder interactive mode — agent={agent_id} session={current_session}")
    print("[INFO] Type /help for commands.")
    while True:
        try:
            line = input("you> ")
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print("\n[INFO] interrupted")
            return

        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"/exit", "/quit"}:
            return
        if stripped == "/help":
            _print_repl_help()
            continue
        if stripped == "/reset":
            current_session = f"corecoder-chat-{uuid4().hex[:8]}"
            print(f"[INFO] session reset -> {current_session}")
            continue
        if stripped == "/session":
            print(current_session)
            continue
        if stripped == "/usage":
            if last_result is None:
                print("[INFO] no previous turn")
            else:
                _print_usage(last_result)
            continue

        result = await runner.run_detailed(
            agent_id=agent_id,
            session_id=current_session,
            input_text=stripped,
        )
        last_result = result
        if result.stop_reason.value == "failed" and result.error_details is not None:
            print(f"agent> [ERROR] {result.error_details.message}")
        else:
            print(f"agent> {result.final_output}")


async def main_async(args: argparse.Namespace) -> int:
    root = Path(__file__).parent
    load_env(root / ".env")
    normalize_env_aliases()

    required_key = os.environ.get("LLM_API_KEY_ENV", "LLM_API_KEY")
    if not os.environ.get(required_key) and os.environ.get("LLM_PROVIDER") != "mock":
        print("[ERROR] LLM_API_KEY not set.")
        print(
            "        Copy .env.example to .env and fill in LLM_API_KEY / "
            "LLM_API_BASE / LLM_MODEL, or provide OPENAI_API_KEY / "
            "OPENAI_BASE_URL / OPENAI_MODEL."
        )
        return 1

    runner = CoreCoderLocalRunner(root / "agent.json")
    try:
        if args.interactive:
            session_id = args.session_id or f"corecoder-chat-{uuid4().hex[:8]}"
            await _run_interactive(
                runner,
                agent_id=args.agent_id,
                session_id=session_id,
            )
        elif args.input:
            session_id = args.session_id or "corecoder-once"
            await _run_one_shot(
                runner,
                agent_id=args.agent_id,
                session_id=session_id,
                text=args.input,
            )
        else:
            session_id = args.session_id or "corecoder-demo"
            await _run_demo_mode(
                runner,
                root=root,
                agent_id=args.agent_id,
                session_id=session_id,
            )
    finally:
        await runner.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
