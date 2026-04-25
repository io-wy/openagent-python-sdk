"""LLM Wiki Agent demo — ingest URLs and query the knowledge base."""

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


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _banner(title: str) -> None:
    bar = "-" * 72
    _safe_print(f"\n{bar}\n{title}\n{bar}")


async def main() -> None:
    root = Path(__file__).parent
    _load_env(root / ".env")

    if not os.environ.get("LLM_API_KEY"):
        print("[ERROR] LLM_API_KEY not set.")
        print("        Copy .env.example to .env and fill in LLM_API_KEY, LLM_API_BASE, LLM_MODEL.")
        return

    _safe_print(f"[INFO] model={os.environ.get('LLM_MODEL')} base={os.environ.get('LLM_API_BASE')}")

    runtime = Runtime.from_config(root / "agent.json")
    _safe_print(f"[INFO] KB sources: {runtime._config.agents[0].id}")

    # Scenario 1: Ingest a URL
    _banner("Scenario 1: Ingest a URL into the knowledge base")
    ingest_out = await runtime.run(
        agent_id="llm-wiki-agent",
        session_id="demo-wiki-1",
        input_text="Ingest https://en.wikipedia.org/wiki/Transformer_(machine_learning_model)",
    )
    _safe_print(f"\n  Result: {ingest_out}")

    # Scenario 2: Query the knowledge base
    _banner("Scenario 2: Ask a question about the ingested content")
    query_out = await runtime.run(
        agent_id="llm-wiki-agent",
        session_id="demo-wiki-1",
        input_text="What is the key innovation of the transformer architecture?",
    )
    _safe_print(f"\n  Answer: {query_out}")

    # Scenario 3: List sources
    _banner("Scenario 3: List all ingested sources")
    list_out = await runtime.run(
        agent_id="llm-wiki-agent",
        session_id="demo-wiki-1",
        input_text="What sources do you have?",
    )
    _safe_print(f"\n  Sources: {list_out}")

    await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
