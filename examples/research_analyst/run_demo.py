"""Run the research-analyst example against the local stub server.

This demo mostly serves as a smoke test that the config parses, all seven
new builtins load, and the session / event-log files are written to
``examples/research_analyst/sessions/``. It uses the mock LLM provider, so
the agent will not drive a meaningful tool-use loop — for that, see the
integration test in tests/integration/.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure repo root is on sys.path when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openagents.runtime.runtime import Runtime

from examples.research_analyst.app.stub_server import start_stub_server


_HERE = Path(__file__).resolve().parent


async def main() -> None:
    sessions_dir = _HERE / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    async with start_stub_server() as base_url:
        print(f"[research_analyst] stub server on {base_url}")
        runtime = Runtime.from_config(_HERE / "agent.json")
        session_id = "demo-session"

        print("[research_analyst] Run #1: research request (mock provider)")
        try:
            first = await runtime.run(
                agent_id="research-analyst",
                session_id=session_id,
                input_text="Research topic-a and write a short note.",
            )
            print(f"  output: {first}")
        except Exception as exc:
            print(f"  [expected with mock provider] run raised: {type(exc).__name__}: {exc}")

        print("[research_analyst] Run #2: follow-up question (rule_based resolver)")
        try:
            second = await runtime.run(
                agent_id="research-analyst",
                session_id=session_id,
                input_text="你刚才查了哪些 URL？",
            )
            print(f"  output: {second}")
        except Exception as exc:
            print(f"  [expected with mock provider] run raised: {type(exc).__name__}: {exc}")

        print(f"[research_analyst] session file:  {sessions_dir / f'{session_id}.jsonl'}")
        print(f"[research_analyst] events log:    {sessions_dir / 'events.ndjson'}")

        if hasattr(runtime, "close"):
            await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
