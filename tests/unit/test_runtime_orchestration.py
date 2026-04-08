import asyncio
import time

import pytest

from openagents.config.loader import load_config_dict
from openagents.interfaces.runtime import RunRequest
from openagents.interfaces.session import SessionArtifact
from openagents.runtime.runtime import Runtime


def _payload(memory_impl: str, pattern_impl: str, *, on_error: str = "continue") -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "runtime-test-agent",
                "memory": {"impl": memory_impl, "on_error": on_error},
                "pattern": {"impl": pattern_impl},
                "llm": {"provider": "mock"},
                "skill": None,
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
async def test_runtime_inject_react_writeback_flow():
    config = load_config_dict(
        _payload(
            "tests.fixtures.runtime_plugins.InjectWritebackMemory",
            "tests.fixtures.runtime_plugins.FinalPattern",
        )
    )
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="s1",
        input_text="hello",
    )

    assert result == "injected=True"
    session_state = await runtime.session_manager.get_state("s1")
    assert session_state.get("memory_written") is True


@pytest.mark.asyncio
async def test_runtime_memory_error_continue():
    config = load_config_dict(
        _payload(
            "tests.fixtures.runtime_plugins.FailingInjectMemory",
            "tests.fixtures.runtime_plugins.FinalPattern",
            on_error="continue",
        )
    )
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="s1",
        input_text="hello",
    )

    assert result == "injected=False"
    assert any(evt.name == "memory.inject_failed" for evt in runtime.event_bus.history)


@pytest.mark.asyncio
async def test_runtime_memory_error_fail():
    config = load_config_dict(
        _payload(
            "tests.fixtures.runtime_plugins.FailingInjectMemory",
            "tests.fixtures.runtime_plugins.FinalPattern",
            on_error="fail",
        )
    )
    runtime = Runtime(config)

    with pytest.raises(RuntimeError, match="inject failed"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="hello")


@pytest.mark.asyncio
async def test_runtime_same_session_serial_execution():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.SlowFinalPattern",
    )
    payload["agents"][0]["pattern"]["config"] = {"delay": 0.05}
    config = load_config_dict(payload)
    runtime = Runtime(config)

    start = time.perf_counter()
    await asyncio.gather(
        runtime.run(agent_id="assistant", session_id="same", input_text="1"),
        runtime.run(agent_id="assistant", session_id="same", input_text="2"),
    )
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.09


@pytest.mark.asyncio
async def test_runtime_applies_skill_prompt_and_metadata():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.PromptAwarePattern",
    )
    payload["agents"][0]["skill"] = {
        "impl": "tests.fixtures.runtime_plugins.RuntimePromptSkill",
        "config": {"focus": "training"},
    }
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="s1",
        input_text="help me tune learning rate",
    )

    assert result["active_skill"] == "RuntimePromptSkill"
    assert result["metadata"]["focus"] == "training"
    assert result["prompt"] == ["You are the training specialist."]


@pytest.mark.asyncio
async def test_runtime_persists_transcript_and_artifacts():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.ArtifactPattern",
    )
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="artifact-session",
            input_text="generate a report",
        )
    )

    transcript = await runtime.session_manager.load_messages("artifact-session")
    artifacts = await runtime.session_manager.list_artifacts("artifact-session")

    assert result.final_output == "artifact-done"
    assert result.stop_reason == "completed"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].name == "report.txt"
    assert [item["role"] for item in transcript] == ["user", "assistant"]
    assert transcript[1]["content"] == "artifact-done"
    assert len(artifacts) == 1
    assert artifacts[0].name == "report.txt"


@pytest.mark.asyncio
async def test_runtime_applies_skill_runtime_augmentation_hooks():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.PromptAwarePattern",
    )
    payload["agents"][0]["skill"] = {
        "impl": "tests.fixtures.runtime_plugins.RuntimeLifecycleSkill",
    }
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="skill-hooks",
        input_text="run the lifecycle hooks",
    )

    assert result["active_skill"] == "RuntimeLifecycleSkill"
    assert result["metadata"]["focus"] == "lifecycle"
    assert result["prompt"] == ["You are the lifecycle specialist."]
    assert result["tools"] == ["skill_calc"]
    assert result["memory_view"]["skill_augmented"] is True
    assert result["state"]["skill_context_augmented"] is True
    assert result["state"]["skill_pre_run"] is True
    assert result["state"]["skill_post_run"] is True


@pytest.mark.asyncio
async def test_runtime_prefers_agent_level_runtime_seams_over_runtime_config():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.ContextAwarePattern",
    )
    payload["runtime"] = {
        "type": "default",
        "config": {
            "context_assembler": {
                "impl": "tests.fixtures.runtime_plugins.SummarizingContextAssembler",
                "config": {"prefix": "runtime-level"},
            }
        },
    }
    payload["agents"][0]["context_assembler"] = {
        "impl": "tests.fixtures.custom_plugins.CustomContextAssembler",
        "config": {"marker": "agent-level"},
    }
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="agent-level-assembler",
        input_text="hello",
    )
    session_state = await runtime.session_manager.get_state("agent-level-assembler")

    assert result["transcript_count"] == 1
    assert result["assembly_metadata"]["marker"] == "agent-level"
    assert session_state["custom_assembler_seen"] is True
    assert session_state["custom_assembler_finalized"] is True


@pytest.mark.asyncio
async def test_runtime_uses_configured_tool_executor():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.ToolCallingPattern",
    )
    payload["runtime"] = {
        "type": "default",
        "config": {
            "tool_executor": {
                "impl": "tests.fixtures.runtime_plugins.PrefixingToolExecutor",
                "config": {"name": "custom-executor"},
            }
        },
    }
    payload["agents"][0]["tools"] = [
        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
    ]
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="custom-executor",
        input_text="hello",
    )

    assert result["executor"] == "custom-executor"
    assert result["data"]["ok"] is True
    assert result["data"]["params"] == {"value": "hello"}


@pytest.mark.asyncio
async def test_runtime_uses_configured_execution_policy():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.ToolCallingPattern",
    )
    payload["runtime"] = {
        "type": "default",
        "config": {
            "execution_policy": {
                "impl": "tests.fixtures.runtime_plugins.DenyToolExecutionPolicy",
                "config": {"deny_tools": ["custom_tool"]},
            }
        },
    }
    payload["agents"][0]["tools"] = [
        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
    ]
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="denied-tool",
            input_text="hello",
        )
    )

    assert result.stop_reason == "failed"
    assert "blocked by DenyToolExecutionPolicy" in (result.error or "")


@pytest.mark.asyncio
async def test_runtime_uses_configured_context_assembler():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.ContextAwarePattern",
    )
    payload["runtime"] = {
        "type": "default",
        "config": {
            "context_assembler": {
                "impl": "tests.fixtures.runtime_plugins.SummarizingContextAssembler",
                "config": {"prefix": "assembled"},
            }
        },
    }
    config = load_config_dict(payload)
    runtime = Runtime(config)

    await runtime.session_manager.append_message(
        "assembled-session",
        {"role": "user", "content": "earlier"},
    )
    await runtime.session_manager.save_artifact(
        "assembled-session",
        SessionArtifact(name="existing.txt", kind="text", payload="seed"),
    )

    result = await runtime.run(
        agent_id="assistant",
        session_id="assembled-session",
        input_text="hello",
    )

    session_state = await runtime.session_manager.get_state("assembled-session")
    artifacts = await runtime.session_manager.list_artifacts("assembled-session")

    assert result["transcript_count"] == 2
    assert result["artifact_names"] == ["existing.txt"]
    assert result["assembly_metadata"]["assembler"] == "assembled"
    assert session_state["assembler_seen"] is True
    assert session_state["assembler_finalized"] is True
    assert any(artifact.name == "assembly-summary.txt" for artifact in artifacts)
