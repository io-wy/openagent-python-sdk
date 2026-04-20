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


async def main() -> None:
    root = Path(__file__).parent
    load_env(root / ".env")

    if not os.environ.get("LLM_API_KEY"):
        print("[ERROR] LLM_API_KEY not set.")
        print("        Copy .env.example to .env and fill in LLM_API_KEY, LLM_API_BASE, LLM_MODEL.")
        return

    runtime = Runtime.from_config(root / "agent.json")
    session_id = "production-coding-demo"

    print("[INFO] Running production-style coding agent example")
    print("[INFO] Workspace:", root / "workspace")
    print()

    # Durable=True opts into auto-checkpoint + auto-resume. On LLMRateLimitError /
    # LLMConnectionError / ToolRateLimitError / ToolUnavailableError the runtime
    # will load the most recent per-step checkpoint and re-invoke the pattern.
    # Bounded by RunBudget.max_resume_attempts (default 3).
    first_result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="production-coding-agent",
            session_id=session_id,
            input_text="create a python-todo-cli project",
            durable=True,
            budget=RunBudget(max_resume_attempts=3),
        )
    )
    print("RUN 1:")
    print(first_result.final_output)
    print()

    second_result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="production-coding-agent",
            session_id=session_id,
            input_text=(
                "what tools did you just call? please continue developing this project"
                " based on the results of the tool calls."
            ),
            durable=True,
            budget=RunBudget(max_resume_attempts=3),
        )
    )
    print("RUN 2:")
    print(second_result.final_output)

    await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
