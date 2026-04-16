from __future__ import annotations

from pathlib import Path
import tomllib

import openagents
import openagents.config as config_module
import openagents.plugins as plugins_module
import pytest

from openagents.config.schema import LLMOptions
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.capabilities import PATTERN_EXECUTE, TOOL_INVOKE, normalize_capabilities, supports
from openagents.interfaces.context import ContextAssemblerPlugin
from openagents.interfaces.events import EventBusPlugin, RuntimeEvent
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.plugin import BasePlugin
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.runtime import RunArtifact, RunRequest, RunResult, RunUsage, RuntimePlugin, StopReason
from openagents.interfaces.session import SessionArtifact, SessionCheckpoint, SessionManagerPlugin
from openagents.interfaces.skills import SessionSkillSummary, SkillsPlugin
from openagents.interfaces.tool import ToolPlugin
from openagents.llm.base import LLMClient, LLMResponse, LLMUsage
from openagents.llm.providers.anthropic import AnthropicClient
from openagents.llm.providers.mock import MockLLMClient
from openagents.llm.providers.openai_compatible import OpenAICompatibleClient
from openagents.llm.registry import create_llm_client


class _SessionStore(SessionManagerPlugin):
    def __init__(self):
        super().__init__(config={}, capabilities=set())
        self._states: dict[str, dict] = {}

    async def session(self, session_id: str):
        raise NotImplementedError

    async def get_state(self, session_id: str) -> dict[str, object]:
        return dict(self._states.get(session_id, {}))

    async def set_state(self, session_id: str, state: dict[str, object]) -> None:
        self._states[session_id] = dict(state)

    async def delete_session(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    async def list_sessions(self) -> list[str]:
        return sorted(self._states)


class _RecordingEventBus(EventBusPlugin):
    def __init__(self):
        super().__init__(config={}, capabilities=set())
        self.events: list[RuntimeEvent] = []

    def subscribe(self, event_name, handler) -> None:
        raise NotImplementedError

    async def emit(self, event_name: str, **payload):
        event = RuntimeEvent(name=event_name, payload=payload)
        self.events.append(event)
        return event

    async def get_history(self, event_name=None, limit=None):
        return self.events

    async def clear_history(self) -> None:
        self.events.clear()


class _EchoLLM(LLMClient):
    async def complete(self, *, messages, model=None, temperature=None, max_tokens=None, tools=None, tool_choice=None, response_format=None):
        _ = (model, temperature, max_tokens, tools, tool_choice)
        return messages[-1]["content"]


class _PatternHarness(PatternPlugin):
    async def execute(self):
        return "done"

    async def react(self):
        return {"type": "final"}


class _ToolWithFallback:
    async def invoke(self, params, context):
        raise RuntimeError("boom")

    async def fallback(self, error, params, context):
        return {"recovered": str(error), "params": params, "context": context.input_text}


class _ToolSuccess:
    async def invoke(self, params, context):
        return {"ok": True, "params": params}

    async def fallback(self, error, params, context):
        raise AssertionError("fallback should not be called")


@pytest.mark.asyncio
async def test_exports_registry_and_capability_helpers_cover_public_surface():
    assert "Runtime" in openagents.__all__
    assert "RunContext" in openagents.__all__
    assert "load_config" in config_module.__all__
    assert "load_agent_plugins" in plugins_module.__all__
    assert "LocalSkillsManager" in openagents.__all__
    assert "SkillsPlugin" in openagents.__all__

    plugin = BasePlugin.from_capabilities(config={"x": 1}, capabilities=[TOOL_INVOKE, " ", None, TOOL_INVOKE])
    assert plugin.config == {"x": 1}
    assert plugin.capability_set() == {TOOL_INVOKE}
    assert plugin.supports(TOOL_INVOKE) is True
    assert normalize_capabilities([PATTERN_EXECUTE, " ", 123, TOOL_INVOKE]) == {PATTERN_EXECUTE, TOOL_INVOKE}
    assert supports(plugin, TOOL_INVOKE) is True
    assert StopReason.COMPLETED.value == "completed"
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.2.0"


@pytest.mark.asyncio
async def test_memory_skills_context_and_event_base_classes_cover_defaults():
    memory = MemoryPlugin()
    skills = SkillsPlugin(config={"search_paths": ["skills"]}, capabilities=set())
    assembler = ContextAssemblerPlugin(config={})
    event_bus = EventBusPlugin(config={}, capabilities=set())

    assert await memory.retrieve("query", object()) == []
    assert await memory.inject(object()) is None
    assert await memory.writeback(object()) is None
    assert await memory.close() is None

    summary = SessionSkillSummary(name="builder", description="desc")
    assert summary.references_loaded == []
    with pytest.raises(NotImplementedError):
        await skills.prepare_session(session_id="s1", session_manager=object())
    with pytest.raises(NotImplementedError):
        await skills.load_references(session_id="s1", skill_name="builder", session_manager=object())
    with pytest.raises(NotImplementedError):
        await skills.run_skill(session_id="s1", skill_name="builder", payload={}, session_manager=object())

    assembly = await assembler.assemble(request={}, session_state={}, session_manager=None)
    assert assembly.transcript == []
    assert await assembler.finalize(request={}, session_state={}, session_manager=None, result="x") == "x"
    assert await event_bus.close() is None


@pytest.mark.asyncio
async def test_pattern_plugin_setup_call_tool_call_llm_compose_prompt_and_artifacts():
    bus = _RecordingEventBus()
    pattern = _PatternHarness(config={})
    llm = _EchoLLM()

    await pattern.setup(
        agent_id="assistant",
        session_id="s1",
        input_text="hello",
        state={"seen": True},
        tools={"ok": _ToolSuccess(), "fallback": _ToolWithFallback()},
        llm_client=llm,
        llm_options={"model": "mock"},
        event_bus=bus,
        transcript=[{"role": "user", "content": "older"}],
        session_artifacts=[SessionArtifact(name="a.txt", payload="a")],
        assembly_metadata={"origin": "test"},
        run_request=RunRequest(agent_id="assistant", session_id="s1", input_text="hello"),
        usage=RunUsage(),
        artifacts=[],
    )

    pattern.context.system_prompt_fragments.extend(["focus", "details"])
    tool_result = await pattern.call_tool("ok", {"value": 1})
    fallback_result = await pattern.call_tool("fallback", {"value": 2})
    llm_result = await pattern.call_llm(messages=[{"role": "user", "content": '{"ok": true}'}])
    prompt = pattern.compose_system_prompt("base")
    await pattern.compress_context()
    pattern.add_artifact(name="report.txt", payload="done", kind="text", metadata={"k": "v"})

    assert tool_result == {"ok": True, "params": {"value": 1}}
    assert fallback_result["recovered"] == "boom"
    assert llm_result == '{"ok": true}'
    assert prompt == "base\n\nfocus\n\ndetails"
    assert pattern.context.usage.tool_calls == 1
    assert pattern.context.usage.llm_calls == 1
    assert pattern.context.usage.total_tokens == 0
    assert pattern.context.assembly_metadata == {"origin": "test"}
    assert len(pattern.context.artifacts) == 1
    assert pattern.context.artifacts[0] == RunArtifact(name="report.txt", kind="text", payload="done", metadata={"k": "v"})
    assert [event.name for event in bus.events] == [
        "tool.called",
        "tool.succeeded",
        "tool.called",
        "tool.failed",
        "llm.called",
        "usage.updated",
        "llm.succeeded",
    ]

    with pytest.raises(KeyError):
        await pattern.call_tool("missing")


@pytest.mark.asyncio
async def test_tool_runtime_and_session_base_classes_cover_default_branches():
    class _ToolHarness(ToolPlugin):
        async def invoke(self, params, context):
            return {"params": params, "context": context}

    tool = _ToolHarness(config={}, capabilities={TOOL_INVOKE})
    runtime = RuntimePlugin(config={}, capabilities=set())
    store = _SessionStore()
    ctx = RunContext[object](
        agent_id="assistant",
        session_id="s1",
        run_id="run-1",
        input_text="hello",
        event_bus=_RecordingEventBus(),
    )

    assert tool.tool_name == "_ToolHarness"
    assert tool.execution_spec().reads_files is False
    assert tool.describe()["name"] == "_ToolHarness"
    assert tool.validate_params({}) == (True, None)
    assert tool.get_dependencies() == []
    assert [chunk async for chunk in tool.invoke_stream({"value": 1}, ctx)] == [
        {"type": "result", "data": {"params": {"value": 1}, "context": ctx}}
    ]
    with pytest.raises(RuntimeError, match="boom"):
        await tool.fallback(RuntimeError("boom"), {}, None)

    assert await runtime.initialize() is None
    assert await runtime.validate() is None
    assert await runtime.health_check() is True
    assert await runtime.pause() is None
    assert await runtime.resume() is None
    assert await runtime.close() is None
    with pytest.raises(NotImplementedError):
        await runtime.run(request=RunRequest(agent_id="assistant", session_id="s1", input_text="hello"))

    await store.append_message("s1", {"role": "user", "content": "hello"})
    await store.save_artifact("s1", SessionArtifact(name="note.txt", payload={"x": 1}))
    checkpoint = await store.create_checkpoint("s1", "cp1")
    loaded_artifacts = await store.list_artifacts("s1")
    loaded_checkpoint = await store.load_checkpoint("s1", "cp1")

    assert await store.list_sessions() == ["s1"]
    assert await store.load_messages("s1") == [{"role": "user", "content": "hello"}]
    assert loaded_artifacts == [SessionArtifact(name="note.txt", kind="generic", payload={"x": 1}, metadata={})]
    assert checkpoint.checkpoint_id == "cp1"
    assert loaded_checkpoint == SessionCheckpoint.from_dict(checkpoint.to_dict())
    await store.delete_session("s1")
    assert await store.list_sessions() == []
    assert await store.close() is None


@pytest.mark.asyncio
async def test_llm_base_and_registry_cover_normalization_merge_and_factory_paths():
    usage = LLMUsage(input_tokens=-1, output_tokens=2, total_tokens=0, metadata={"a": 1})
    normalized = usage.normalized()
    merged = normalized.merge(LLMUsage(input_tokens=5, metadata={"b": 2}))
    response = LLMResponse(output_text='{"value": 1}', usage=normalized)

    llm = _EchoLLM()
    generated = await llm.generate(
        messages=[{"role": "user", "content": '{"value": 2}'}],
        response_format={"type": "json_schema"},
    )
    streamed = [chunk async for chunk in llm.complete_stream(messages=[{"role": "user", "content": "hello"}])]

    assert normalized.input_tokens == 0
    assert normalized.total_tokens == 2
    assert merged.input_tokens == 5
    assert merged.output_tokens == 2
    assert merged.metadata == {"a": 1, "b": 2}
    assert response.output_text == '{"value": 1}'
    assert generated.structured_output == {"value": 2}
    assert llm.get_last_response() == generated
    assert streamed[0].type == "content_block_delta"
    assert streamed[1].type == "message_stop"
    assert await llm.aclose() is None

    assert create_llm_client(None) is None
    assert isinstance(create_llm_client(LLMOptions(provider="mock")), MockLLMClient)

    anthropic = create_llm_client(LLMOptions(provider="anthropic"))
    assert isinstance(anthropic, AnthropicClient)
    assert anthropic._messages_endpoint().endswith("/v1/messages")

    compatible = create_llm_client(
        LLMOptions(provider="openai_compatible", api_base="https://example.invalid/v1", model="gpt-test")
    )
    assert isinstance(compatible, OpenAICompatibleClient)

    with pytest.raises(ConfigError):
        create_llm_client(LLMOptions(provider="openai_compatible"))
    with pytest.raises(ConfigError):
        create_llm_client(LLMOptions(provider="unsupported"))


def test_new_030_exports():
    from openagents import (
        ModelRetryError,
        OutputValidationError,
        RunStreamChunk,
        RunStreamChunkKind,
    )

    assert RunStreamChunk.__name__ == "RunStreamChunk"
    assert RunStreamChunkKind.RUN_FINISHED.value == "run.finished"
    assert issubclass(OutputValidationError, Exception)
    assert issubclass(ModelRetryError, Exception)
