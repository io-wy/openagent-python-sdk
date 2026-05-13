"""Run the CoreCoder example agent against the bundled buggy workspace.

Usage::

    cp examples/corecoder_agent/.env.example examples/corecoder_agent/.env
    # edit .env with your provider details
    uv run python examples/corecoder_agent/run_demo.py

The agent is briefed with ``workspace/TASK.md`` and asked to fix the bugs
in ``workspace/stats.py`` until ``workspace/test_stats.py`` passes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openagents.interfaces.runtime import RunBudget, RunRequest
from openagents.runtime.runtime import Runtime


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


def render_task_brief(workspace: Path) -> str:
    task_md = workspace / "TASK.md"
    body = task_md.read_text(encoding="utf-8") if task_md.exists() else ""
    return (
        "There are bugs in workspace/stats.py that make workspace/test_stats.py "
        "fail. Find and fix them with edit_file (one minimal edit per bug), then "
        "verify by running the test suite. The full brief is below.\n\n"
        f"---\n{body}"
    )


async def main() -> None:
    root = Path(__file__).parent
    load_env(root / ".env")

    if not os.environ.get("LLM_API_KEY"):
        print("[ERROR] LLM_API_KEY not set.")
        print(
            "        Copy .env.example to .env and fill in LLM_API_KEY, "
            "LLM_API_BASE, LLM_MODEL."
        )
        return

    runtime = Runtime.from_config(root / "agent.json")
    session_id = "corecoder-demo"

    print("[INFO] Running CoreCoder example agent")
    print("[INFO] Workspace:", root / "workspace")
    print()

    brief = render_task_brief(root / "workspace")
    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="corecoder",
            session_id=session_id,
            input_text=brief,
            durable=True,
            budget=RunBudget(
                max_resume_attempts=2,
                max_validation_retries=3,
            ),
        )
    )
    print("=" * 70)
    print("FINAL ANSWER:")
    print("=" * 70)
    print(result.final_output)
    print()
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
    print()
    print("[INFO] Verify the fix yourself:")
    print(f"       cd {root / 'workspace'}")
    print("       python -m unittest test_stats.py")


if __name__ == "__main__":
    asyncio.run(main())
