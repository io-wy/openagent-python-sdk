"""Multi-agent demo driven by a real LLM (MINIMAX / Anthropic-compatible endpoint).

Needs ``LLM_API_KEY`` / ``LLM_API_BASE`` / ``LLM_MODEL`` (see ``.env.example``).
Shows an orchestrator that delegates a research subtask to a specialist and
a triage agent that transfers billing issues to a billing agent.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openagents.runtime.runtime import Runtime  # noqa: E402


def _load_env(path: Path) -> None:
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


def _banner(title: str) -> None:
    bar = "-" * 72
    print(f"\n{bar}\n{title}\n{bar}")


async def main() -> None:
    root = Path(__file__).parent
    _load_env(root / ".env")

    if not os.environ.get("LLM_API_KEY"):
        print("[ERROR] LLM_API_KEY not set.")
        print("        Copy .env.example to .env and fill in LLM_API_KEY, LLM_API_BASE, LLM_MODEL.")
        return

    print(f"[INFO] model={os.environ.get('LLM_MODEL')} base={os.environ.get('LLM_API_BASE')}")

    runtime = Runtime.from_config(root / "agent_real.json")
    print("[INFO] multi_agent:", runtime._config.multi_agent)

    # Scenario 1: Orchestrator pattern (delegate)
    _banner("Scenario 1: Orchestrator delegates a research task to the specialist")
    orchestrator_out = await runtime.run(
        agent_id="orchestrator",
        session_id="demo-real-orchestrator",
        input_text=(
            "I need a short fact check: what is the boiling point of water at sea level "
            "in Celsius? If you need a lookup, delegate to the specialist before answering."
        ),
    )
    print("\n  Orchestrator final output:")
    print(f"    {orchestrator_out}")

    # Scenario 2: Triage / handoff pattern (transfer)
    _banner("Scenario 2: Triage transfers a billing complaint to billing_agent")
    triage_out = await runtime.run(
        agent_id="triage",
        session_id="demo-real-triage",
        input_text=(
            "Hi, I want a refund for order #ORD-9821. It was charged twice last week. Please take care of this."
        ),
    )
    print("\n  Triage final output (after handoff):")
    print(f"    {triage_out}")

    await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
