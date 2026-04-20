"""Integration: token-budget assembler feeds through a full runtime (Task 33)."""

from __future__ import annotations

import pytest

from openagents.config.loader import load_config_dict
from openagents.interfaces.session import SessionArtifact
from openagents.runtime.runtime import Runtime


def _payload(assembler_type: str, max_tokens: int = 120) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "context-budget-e2e",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {"impl": "tests.fixtures.runtime_plugins.ContextAwarePattern"},
                "context_assembler": {
                    "type": assembler_type,
                    "config": {
                        "max_input_tokens": max_tokens,
                        "reserve_for_response": 0,
                        "max_artifacts": 2,
                    },
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 8,
                    "step_timeout_ms": 1000,
                    "session_queue_size": 100,
                    "event_queue_size": 100,
                },
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("assembler", ["head_tail", "sliding_window", "importance_weighted"])
async def test_token_budget_assembler_end_to_end(assembler):
    runtime = Runtime(load_config_dict(_payload(assembler)))
    for idx in range(10):
        await runtime.session_manager.append_message(
            "ctx-session",
            {"role": "user", "content": f"message-{idx}"},
        )
    for name in ("a.txt", "b.txt", "c.txt"):
        await runtime.session_manager.save_artifact(
            "ctx-session", SessionArtifact(name=name, kind="text", payload=name)
        )

    result = await runtime.run(
        agent_id="assistant",
        session_id="ctx-session",
        input_text="probe",
    )
    assembly_md = result["assembly_metadata"]
    # Metadata records the token counter, budget, and kept/omitted statistics.
    assert assembly_md["budget_input_tokens"] == 120
    assert "token_counter" in assembly_md
    assert assembly_md["omitted_artifacts"] >= 1  # we had 3, max_artifacts=2
