"""Run CoreCoder against a multi-file ML microservice task.

Usage::

    cp examples/corecoder_agent/.env.example examples/corecoder_agent/.env
    # edit .env with your provider details
    uv run python examples/corecoder_agent/run_ml_demo.py

This is a stress test for the CoreCoder example: it briefs the agent with
``ml_workspace/TASK.md`` and asks it to build an end-to-end iris-classification
microservice (data → train → eval → FastAPI inference → pytest).

Differences vs. ``run_demo.py``:

* Loads ``agent.json`` as a dict and bumps step / context budgets up
  (``pattern.max_steps`` 20 → 60, ``runtime.max_steps`` 30 → 80,
  ``context_assembler.max_input_tokens`` 16000 → 24000).
* Uses an isolated ``.ml_agent_memory`` directory and a distinct
  ``session_id`` so the two demos do not share persistent memory state.
* Briefs the agent from ``ml_workspace/TASK.md`` instead of ``workspace/``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openagents.config.loader import _expand_env_vars  # noqa: E402  (intentional)
from openagents.interfaces.runtime import RunBudget, RunRequest  # noqa: E402
from openagents.runtime.runtime import Runtime  # noqa: E402


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


def load_agent_payload(config_path: Path) -> dict:
    raw = config_path.read_text(encoding="utf-8")
    expanded = _expand_env_vars(raw, source=config_path)
    return json.loads(expanded)


def boost_budgets(payload: dict) -> dict:
    """Bump step + context budgets so a multi-file ML task can fit."""
    cfg = copy.deepcopy(payload)
    for agent in cfg.get("agents", []):
        agent_id = agent.get("id", "")
        is_main = agent_id == "corecoder"

        pattern_cfg = agent.setdefault("pattern", {}).setdefault("config", {})
        pattern_cfg["max_steps"] = 60 if is_main else 30

        runtime_cfg = agent.setdefault("runtime", {})
        runtime_cfg["max_steps"] = 80 if is_main else 40
        runtime_cfg["step_timeout_ms"] = 600_000

        ctx_cfg = agent.setdefault("context_assembler", {}).setdefault("config", {})
        ctx_cfg["max_input_tokens"] = 24_000 if is_main else 16_000

        if is_main:
            for mem in (
                agent.get("memory", {}).get("config", {}).get("memories", [])
            ):
                if mem.get("impl", "").endswith("CoreCoderMemory"):
                    mem.setdefault("config", {})["storage_dir"] = (
                        "examples/corecoder_agent/.ml_agent_memory"
                    )
    return cfg


def render_task_brief(workspace: Path) -> str:
    task_md = workspace / "TASK.md"
    body = task_md.read_text(encoding="utf-8") if task_md.exists() else ""
    return (
        "Build the end-to-end ML microservice described below in "
        "`examples/corecoder_agent/ml_workspace/`. Read TASK.md carefully "
        "before you start; verify each acceptance command yourself with the "
        "bash tool before declaring success.\n\n"
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

    payload = boost_budgets(load_agent_payload(root / "agent.json"))
    runtime = Runtime.from_dict(payload)
    session_id = "corecoder-ml-demo"

    print("[INFO] Running CoreCoder ML demo")
    print("[INFO] Workspace:", root / "ml_workspace")
    print("[INFO] Boosted: pattern.max_steps=60, runtime.max_steps=80,"
          " context_assembler.max_input_tokens=24000")
    print()

    brief = render_task_brief(root / "ml_workspace")
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
    print("[INFO] Verify the output yourself:")
    print(f"       cd {root / 'ml_workspace'}")
    print("       uv run python -m src.train")
    print("       uv run python -m src.evaluate")
    print("       uv run python -m pytest -q")


if __name__ == "__main__":
    asyncio.run(main())
