from __future__ import annotations

import asyncio
import os
from pathlib import Path

from openagents.runtime import Runtime


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


async def main() -> None:
    demo_dir = Path(__file__).parent
    load_env(demo_dir / ".env")

    if not os.environ.get("LLM_API_KEY"):
        print("[ERROR] LLM_API_KEY not set!")
        print("        Copy .env.example to .env and fill in LLM_API_KEY, LLM_API_BASE, LLM_MODEL.")
        return

    print(f"[INFO] Using model={os.environ.get('LLM_MODEL')} at {os.environ.get('LLM_API_BASE')}\n")

    config_path = demo_dir / "agent.json"
    runtime = Runtime.from_config(config_path)

    out1 = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="hello",
    )
    print("RUN 1:", out1)

    out2 = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="/tool search memory injection",
    )
    print("RUN 2:", out2)


if __name__ == "__main__":
    asyncio.run(main())
