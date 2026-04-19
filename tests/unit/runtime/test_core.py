"""Tests for Runtime core functionality."""


import pytest

import openagents.llm.registry as llm_registry
from openagents.config.loader import load_config_dict
from openagents.config.schema import AgentDefinition, AppConfig
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.runtime import RunRequest
from openagents.interfaces.session import SessionArtifact
from openagents.llm.base import LLMClient, LLMResponse, LLMUsage
from openagents.runtime.runtime import Runtime


def _minimal_config(agent_id: str = "test_agent") -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": agent_id,
                "name": "Test Agent",
                "memory": {"impl": "openagents.plugins.builtin.memory.buffer.BufferMemory", "on_error": "continue"},
                "pattern": {"impl": "openagents.plugins.builtin.pattern.react.ReActPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 3,
                    "step_timeout_ms": 1000,
                    "session_queue_size": 10,
                    "event_queue_size": 10,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_runtime_init_loads_builtin_components():
    """Runtime initialisation wires the default builtin components."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    assert runtime.event_bus is not None
    assert runtime.session_manager is not None
    assert runtime.skills_manager is not None
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_accepts_partial_appconfig():
    """Omitting top-level runtime/session/events/skills still works."""
    from openagents.config.schema import (
        AgentDefinition,
        LLMOptions,
        MemoryRef,
        PatternRef,
        RuntimeOptions,
    )

    agent = AgentDefinition(
        id="partial_agent",
        name="Partial Agent",
        memory=MemoryRef(impl="openagents.plugins.builtin.memory.buffer.BufferMemory", on_error="continue"),
        pattern=PatternRef(impl="openagents.plugins.builtin.pattern.react.ReActPattern"),
        llm=LLMOptions(provider="mock"),
        tools=[],
        runtime=RuntimeOptions(max_steps=3, step_timeout_ms=1000),
    )
    config = AppConfig(agents=[agent])  # runtime/session/events/skills all defaulted
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(agent_id="partial_agent", session_id="s1", input_text="hello")
    )
    assert result.stop_reason == "completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_run_detailed_rejects_non_runresult():
    """RuntimePlugin.run contract: must return RunResult."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    async def _bogus_run(**_kwargs):
        return "not a RunResult"

    runtime._runtime.run = _bogus_run  # type: ignore[method-assign]

    with pytest.raises(TypeError, match="must return RunResult"):
        await runtime.run_detailed(
            request=RunRequest(agent_id="test_agent", session_id="s1", input_text="hello")
        )
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_run_unknown_agent():
    """Test that running unknown agent raises ConfigError."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    with pytest.raises(ConfigError, match="Unknown agent id"):
        await runtime.run(agent_id="nonexistent", session_id="s1", input_text="hello")

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_session_count():
    """Test session count tracking."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    assert runtime.get_session_count() == 0

    # Create a session by getting plugins
    runtime._get_plugins_for_session("s1", "test_agent")

    assert runtime.get_session_count() == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_plugins_for_session():
    """Test per-session plugin isolation."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    plugins1 = runtime._get_plugins_for_session("s1", "test_agent")
    plugins2 = runtime._get_plugins_for_session("s2", "test_agent")

    # Different sessions should have different plugin instances
    assert plugins1 is not plugins2

    # Same session should return same instance
    plugins1_again = runtime._get_plugins_for_session("s1", "test_agent")
    assert plugins1 is plugins1_again

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_plugins_unknown_agent():
    """Test that getting plugins for unknown agent raises ConfigError."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    with pytest.raises(ConfigError, match="Unknown agent id"):
        runtime._get_plugins_for_session("s1", "nonexistent")

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_list_agents():
    """Test listing all agents."""
    config = load_config_dict(_minimal_config("agent1"))
    # Add second agent directly to config
    config.agents.append(
        AgentDefinition(
            id="agent2",
            name="Agent 2",
            memory=config.agents[0].memory,
            pattern=config.agents[0].pattern,
            llm=config.agents[0].llm,
            tools=[],
        )
    )
    runtime = Runtime(config)

    agents = await runtime.list_agents()

    assert len(agents) == 2
    assert {"id": "agent1", "name": "Test Agent"} in agents
    assert {"id": "agent2", "name": "Agent 2"} in agents
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_agent_info():
    """Test getting agent info."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Get plugins first to populate session cache
    runtime._get_plugins_for_session("s1", "test_agent")

    info = await runtime.get_agent_info("test_agent")

    assert info is not None
    assert info["id"] == "test_agent"
    assert info["name"] == "Test Agent"
    assert "memory" in info
    assert "pattern" in info
    assert "tools" in info
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_agent_info_unknown():
    """Test getting info for unknown agent."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    info = await runtime.get_agent_info("nonexistent")
    assert info is None
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reload_no_config_path():
    """Test reload without config path raises error."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    with pytest.raises(ConfigError, match="Cannot reload"):
        await runtime.reload()

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reload_agent_unknown():
    """Test reload_agent with unknown agent raises error."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    with pytest.raises(ConfigError, match="Unknown agent id"):
        await runtime.reload_agent("nonexistent")

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_close_session():
    """Test closing a specific session."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Create sessions
    runtime._get_plugins_for_session("s1", "test_agent")
    runtime._get_plugins_for_session("s2", "test_agent")

    assert runtime.get_session_count() == 2

    await runtime.close_session("s1")

    assert runtime.get_session_count() == 1

    # Closing non-existent session should not error
    await runtime.close_session("nonexistent")

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_close():
    """Test close cleans up all resources."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Create sessions
    runtime._get_plugins_for_session("s1", "test_agent")
    runtime._get_plugins_for_session("s2", "test_agent")

    await runtime.close()

    # Plugins should be cleaned (memory.close() called)
    # Note: session cache dictionary is not cleared, but plugins are closed


@pytest.mark.asyncio
async def test_runtime_run_with_asyncio():
    """Test async run method."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Use await instead of run_sync
    result = await runtime.run(agent_id="test_agent", session_id="s1", input_text="hello")

    # The mock LLM should return something
    assert result is not None

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_run_builds_request_with_deps(monkeypatch):
    """Test runtime facade threads deps into RunRequest."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)
    captured: dict[str, RunRequest] = {}
    deps = {"token": "abc"}

    async def _fake_run_detailed(*, request: RunRequest):
        captured["request"] = request
        from openagents.interfaces.runtime import RunResult

        return RunResult(run_id=request.run_id, final_output=request.deps)

    monkeypatch.setattr(runtime, "run_detailed", _fake_run_detailed)

    result = await runtime.run(
        agent_id="test_agent",
        session_id="s1",
        input_text="hello",
        deps=deps,
    )

    assert result is deps
    assert captured["request"].deps is deps


@pytest.mark.asyncio
async def test_runtime_properties():
    """Test runtime properties."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Test that properties return expected types
    assert runtime.event_bus is not None
    assert runtime.session_manager is not None

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_multiple_sessions_isolation():
    """Test that multiple sessions are properly isolated."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Create multiple sessions for same agent
    p1 = runtime._get_plugins_for_session("s1", "test_agent")
    p2 = runtime._get_plugins_for_session("s2", "test_agent")
    p3 = runtime._get_plugins_for_session("s3", "test_agent")

    # All should be different instances
    assert p1 is not p2
    assert p2 is not p3
    assert p1 is not p3

    assert runtime.get_session_count() == 3

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reload_agent():
    """Test reload_agent clears plugin cache."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    # Create a session
    plugins1 = runtime._get_plugins_for_session("s1", "test_agent")

    # Reload the agent
    await runtime.reload_agent("test_agent")

    # Session should still exist but plugins cleared
    # Creating new session should get fresh plugins
    plugins2 = runtime._get_plugins_for_session("s1", "test_agent")

    # Should be different instances after reload
    assert plugins1 is not plugins2

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_get_agent_info_no_plugins():
    """Test get_agent_info when no plugins loaded yet."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    info = await runtime.get_agent_info("test_agent")

    assert info is not None
    # When no plugins loaded, loaded_plugins should be None
    assert info["loaded_plugins"]["memory"] is None

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_run_detailed_returns_structured_result():
    """Test structured runtime result path."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="test_agent",
            session_id="s1",
            input_text="hello",
        )
    )

    assert result.run_id
    assert result.final_output is not None
    assert result.stop_reason == "completed"
    assert result.metadata["agent_id"] == "test_agent"

    await runtime.close()


@pytest.mark.asyncio
async def test_session_manager_supports_artifacts_and_checkpoints():
    """Test extended session manager contract."""
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)

    await runtime.session_manager.append_message("s1", {"role": "user", "content": "hello"})
    await runtime.session_manager.save_artifact(
        "s1",
        SessionArtifact(name="note.txt", kind="text", payload="payload"),
    )
    checkpoint = await runtime.session_manager.create_checkpoint("s1", "cp1")

    messages = await runtime.session_manager.load_messages("s1")
    artifacts = await runtime.session_manager.list_artifacts("s1")
    loaded = await runtime.session_manager.load_checkpoint("s1", "cp1")

    assert messages == [{"role": "user", "content": "hello"}]
    assert len(artifacts) == 1
    assert artifacts[0].name == "note.txt"
    assert checkpoint.checkpoint_id == "cp1"
    assert loaded is not None
    assert loaded.checkpoint_id == "cp1"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_context_assembler_dependency():
    """Test runtime config rejects context assembler without required methods."""
    payload = _minimal_config()
    payload["runtime"] = {
        "type": "default",
        "config": {
            "context_assembler": {
                "impl": "tests.fixtures.runtime_plugins.BadContextAssembler",
            }
        },
    }
    config = load_config_dict(payload)
    runtime = Runtime(config)

    with pytest.raises(TypeError, match="must implement 'assemble'"):
        await runtime.run(agent_id="test_agent", session_id="s1", input_text="hello")


class _UsageReportingClient(LLMClient):
    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice, response_format)
        return LLMResponse(
            output_text='{"type":"final","content":"usage-aware"}',
            usage=LLMUsage(input_tokens=8, output_tokens=4, total_tokens=12),
        )


def _preflight_agent_config(tool_impl_path: str, tool_id: str) -> AppConfig:
    from openagents.config.schema import (
        AgentDefinition,
        LLMOptions,
        MemoryRef,
        PatternRef,
        RuntimeOptions,
        ToolRef,
    )

    agent = AgentDefinition(
        id="preflight_agent",
        name="Preflight Agent",
        memory=MemoryRef(
            impl="openagents.plugins.builtin.memory.buffer.BufferMemory",
            on_error="continue",
        ),
        pattern=PatternRef(impl="openagents.plugins.builtin.pattern.react.ReActPattern"),
        llm=LLMOptions(provider="mock"),
        tools=[ToolRef(id=tool_id, impl=tool_impl_path)],
        runtime=RuntimeOptions(max_steps=3, step_timeout_ms=1000),
    )
    return AppConfig(agents=[agent])


@pytest.mark.asyncio
async def test_preflight_failure_maps_to_failed_run_result():
    """A tool whose preflight raises PermanentToolError turns into a
    RunResult with stop_reason=FAILED; the pattern loop does not run."""
    config = _preflight_agent_config(
        "tests.fixtures.preflight_tools.FailingPreflightTool",
        "failing_preflight_tool",
    )
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="preflight_agent",
            session_id="preflight-session",
            input_text="hello",
        )
    )

    assert result.stop_reason == "failed"
    assert result.final_output is None
    assert "failing_preflight_tool" in (result.error or "")

    await runtime.close()


@pytest.mark.asyncio
async def test_preflight_runs_once_per_session_for_tool_with_override():
    """Preflight fires once per session when the tool overrides the hook."""
    from tests.fixtures import preflight_tools

    preflight_tools.reset()
    config = _preflight_agent_config(
        "tests.fixtures.preflight_tools.RecordingPreflightTool",
        "recording_preflight_tool",
    )
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="preflight_agent",
            session_id="noop-session",
            input_text="hello",
        )
    )

    assert result.stop_reason == "completed"
    assert preflight_tools.PREFLIGHT_CALLS == ["recording_preflight_tool"]

    await runtime.close()


@pytest.mark.asyncio
async def test_preflight_dedup_across_runs_on_same_session():
    """Second run on the same session skips preflight (cached ok)."""
    from tests.fixtures import preflight_tools

    preflight_tools.reset()
    config = _preflight_agent_config(
        "tests.fixtures.preflight_tools.RecordingPreflightTool",
        "recording_preflight_tool",
    )
    runtime = Runtime(config)

    for _ in range(2):
        result = await runtime.run_detailed(
            request=RunRequest(
                agent_id="preflight_agent",
                session_id="dedup-session",
                input_text="hello",
            )
        )
        assert result.stop_reason == "completed"

    # Preflight was called exactly once despite two runs on the same session.
    assert preflight_tools.PREFLIGHT_CALLS == ["recording_preflight_tool"]

    preflight_events = [
        evt for evt in runtime.event_bus.history if evt.name == "tool.preflight"
    ]
    assert len(preflight_events) == 2
    assert preflight_events[0].payload["result"] == "ok"
    assert preflight_events[1].payload["result"] == "cached-ok"

    await runtime.close()


@pytest.mark.asyncio
async def test_preflight_dedup_does_not_cross_sessions():
    """Different session_ids maintain independent preflight caches."""
    from tests.fixtures import preflight_tools

    preflight_tools.reset()
    config = _preflight_agent_config(
        "tests.fixtures.preflight_tools.RecordingPreflightTool",
        "recording_preflight_tool",
    )
    runtime = Runtime(config)

    for sid in ("session-A", "session-B"):
        result = await runtime.run_detailed(
            request=RunRequest(
                agent_id="preflight_agent",
                session_id=sid,
                input_text="hello",
            )
        )
        assert result.stop_reason == "completed"

    # Two distinct sessions → two preflight invocations.
    assert preflight_tools.PREFLIGHT_CALLS == [
        "recording_preflight_tool",
        "recording_preflight_tool",
    ]

    await runtime.close()


@pytest.mark.asyncio
async def test_preflight_default_no_op_does_not_break_runtime():
    """A tool without a preflight override still runs through the runtime cleanly."""
    config = _preflight_agent_config(
        "tests.fixtures.preflight_tools.NoOverrideTool",
        "no_override_tool",
    )
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="preflight_agent",
            session_id="noop2-session",
            input_text="hello",
        )
    )

    assert result.stop_reason == "completed"

    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_run_detailed_accumulates_llm_usage_from_generate(monkeypatch):
    config = load_config_dict(_minimal_config())
    runtime = Runtime(config)
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: _UsageReportingClient())

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="test_agent",
            session_id="usage-session",
            input_text="hello",
        )
    )

    assert result.final_output == "usage-aware"
    assert result.usage.llm_calls == 1
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 4
    assert result.usage.total_tokens == 12

    await runtime.close()
