from __future__ import annotations

import json

import pytest

import openagents.llm.registry as llm_registry
from openagents.llm.base import LLMChunk, LLMClient, LLMUsage
from openagents.interfaces.runtime import RunRequest
from openagents.runtime.runtime import Runtime


class _StreamingDeltaClient(LLMClient):
    def __init__(self) -> None:
        self.last_tools = None
        self.last_tool_choice = None

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> str:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        raise AssertionError("ClaudeCodePattern should use complete_stream(), not complete()")

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ):
        _ = (messages, model, temperature, max_tokens)
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        yield LLMChunk(
            type="content_block_delta",
            content={"type": "text", "text": "ignored"},
            delta={"type": "text_delta", "text": "Hello"},
        )
        yield LLMChunk(
            type="message_stop",
            content={"stop_reason": "end_turn"},
            usage=LLMUsage(input_tokens=9, output_tokens=4, total_tokens=13),
        )


def _payload() -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "Assistant",
                "memory": {"type": "buffer"},
                "pattern": {
                    "impl": "openagent_cli.plugins.patterns.claude_code_pattern.ClaudeCodePattern",
                    "config": {
                        "max_steps": 8,
                        "compact_threshold": 0.8,
                        "max_context_tokens": 160000,
                        "max_output_tokens": 1024,
                    },
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {"max_steps": 8, "step_timeout_ms": 1000},
            }
        ],
    }


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_collects_text_from_chunk_delta(
    monkeypatch,
    tmp_path,
):
    client = _StreamingDeltaClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(_payload()), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern",
            input_text="hello",
        )
    finally:
        await runtime.close()

    assert result == "Hello"
    assert client.last_tools == []
    assert client.last_tool_choice is None


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_reports_stream_usage(
    monkeypatch,
    tmp_path,
):
    client = _StreamingDeltaClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(_payload()), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run_detailed(
            request=RunRequest(
                agent_id="assistant",
                session_id="claude-code-pattern-usage",
                input_text="hello",
            )
        )
    finally:
        await runtime.close()

    assert result.final_output == "Hello"
    assert result.usage.llm_calls == 1
    assert result.usage.input_tokens == 9
    assert result.usage.output_tokens == 4
    assert result.usage.total_tokens == 13


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_passes_tool_schemas_to_llm(
    monkeypatch,
    tmp_path,
):
    client = _StreamingDeltaClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    payload = _payload()
    payload["agents"][0]["tools"] = [
        {"id": "read", "impl": "openagent_cli.plugins.tools.read_tool.ReadTool"},
    ]
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-tools",
            input_text="read the readme",
        )
    finally:
        await runtime.close()

    assert result == "Hello"
    assert client.last_tool_choice is None
    assert client.last_tools == [
        {
            "name": "read",
            "description": "Read the contents of a file from the filesystem.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        }
    ]


class _ToolUseStreamingClient(LLMClient):
    def __init__(self, expected_path: str) -> None:
        self.expected_path = expected_path
        self.calls = 0
        self.last_tools = None

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> str:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        raise AssertionError("ClaudeCodePattern should use complete_stream(), not complete()")

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ):
        _ = (model, temperature, max_tokens, tool_choice)
        self.calls += 1
        self.last_tools = tools
        if self.calls == 1:
            yield LLMChunk(type="content_block_start", content={"type": "tool_use", "id": "toolu_1", "name": "read"})
            yield LLMChunk(
                type="content_block_delta",
                delta={"type": "input_json_delta", "partial_json": json.dumps({"path": self.expected_path})},
            )
            yield LLMChunk(type="message_stop", content={"stop_reason": "tool_use"})
            return

        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"][0]["type"] == "tool_result"
        assert "README line 1" in messages[-1]["content"][0]["content"]
        yield LLMChunk(type="content_block_delta", delta={"type": "text_delta", "text": "README line 1"})
        yield LLMChunk(type="message_stop", content={"stop_reason": "end_turn"})


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_executes_tool_use_and_keeps_tool_result(
    monkeypatch,
    tmp_path,
):
    readme_path = tmp_path / "README.md"
    readme_path.write_text("README line 1\nREADME line 2\n", encoding="utf-8")

    client = _ToolUseStreamingClient(str(readme_path))
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    payload = _payload()
    payload["agents"][0]["tools"] = [
        {"id": "read", "impl": "openagent_cli.plugins.tools.read_tool.ReadTool"},
    ]
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-tool-roundtrip",
            input_text="read the readme",
        )
    finally:
        await runtime.close()

    assert result == "README line 1"
    assert client.calls == 2
    assert client.last_tools and client.last_tools[0]["name"] == "read"


class _EmptyThenTextStreamingClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> str:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        raise AssertionError("ClaudeCodePattern should use complete_stream(), not complete()")

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ):
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        self.calls += 1
        if self.calls == 1:
            yield LLMChunk(type="message_start", content={"type": "message_start"})
            yield LLMChunk(type="message_stop", content={})
            return

        yield LLMChunk(type="content_block_delta", delta={"type": "text_delta", "text": "retry ok"})
        yield LLMChunk(type="message_stop", content={"stop_reason": "end_turn"})


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_retries_empty_stream_once(
    monkeypatch,
    tmp_path,
):
    client = _EmptyThenTextStreamingClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(_payload()), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-empty-stream",
            input_text="hello",
        )
    finally:
        await runtime.close()

    assert result == "retry ok"
    assert client.calls == 2


class _GlobOnlyStreamingClient(LLMClient):
    def __init__(self, root: str) -> None:
        self.root = root
        self.calls = 0

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> str:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        raise AssertionError("ClaudeCodePattern should use complete_stream(), not complete()")

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ):
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        self.calls += 1
        yield LLMChunk(type="content_block_start", content={"type": "tool_use", "id": "toolu_1", "name": "glob"})
        yield LLMChunk(
            type="content_block_delta",
            delta={"type": "input_json_delta", "partial_json": json.dumps({"pattern": "README*", "root": self.root})},
        )
        yield LLMChunk(type="message_stop", content={"stop_reason": "tool_use"})


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_reads_single_readme_after_glob_without_second_llm_turn(
    monkeypatch,
    tmp_path,
):
    readme = tmp_path / "README.md"
    readme.write_text("README line 1\nREADME line 2\n", encoding="utf-8")

    client = _GlobOnlyStreamingClient(str(tmp_path))
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    payload = _payload()
    payload["agents"][0]["tools"] = [
        {"id": "read", "impl": "openagent_cli.plugins.tools.read_tool.ReadTool"},
        {"id": "glob", "impl": "openagent_cli.plugins.tools.glob_tool.GlobTool"},
    ]
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-readme-fallback",
            input_text="read the readme in this directory",
        )
    finally:
        await runtime.close()

    assert "README line 1" in result
    assert client.calls == 1


class _AlwaysEmptyStreamingClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> str:
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        raise AssertionError("ClaudeCodePattern should use complete_stream(), not complete()")

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ):
        _ = (messages, model, temperature, max_tokens, tools, tool_choice)
        self.calls += 1
        yield LLMChunk(type="message_start", content={"type": "message_start"})
        yield LLMChunk(type="message_stop", content={})


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_raises_explicit_error_on_persistent_empty_response(
    monkeypatch,
    tmp_path,
):
    client = _AlwaysEmptyStreamingClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(_payload()), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        with pytest.raises(RuntimeError, match="empty response") as exc_info:
            await runtime.run(
                agent_id="assistant",
                session_id="claude-code-pattern-persistent-empty",
                input_text="hello",
            )
    finally:
        await runtime.close()

    message = str(exc_info.value)
    assert "stop_reason=<none>" in message
    assert "retries=2" in message
    assert "tools=0" in message
    assert "history_items=0" in message
    assert "last_history_tools=[]" in message
    assert "input='hello'" in message
    assert client.calls == 2


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_uses_followup_resolver_before_llm(
    monkeypatch,
    tmp_path,
):
    client = _AlwaysEmptyStreamingClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    payload = _payload()
    payload["agents"][0]["followup_resolver"] = {
        "impl": "tests.fixtures.custom_plugins.CustomFollowupResolver",
        "config": {"when_input": "what did you do", "result": "from followup resolver"},
    }
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-followup-seam",
            input_text="what did you do",
        )
    finally:
        await runtime.close()

    assert result == "from followup resolver"
    assert client.calls == 0


@pytest.mark.asyncio
async def test_runtime_from_config_claude_code_pattern_uses_response_repair_policy_after_empty_response(
    monkeypatch,
    tmp_path,
):
    client = _AlwaysEmptyStreamingClient()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

    payload = _payload()
    payload["agents"][0]["response_repair_policy"] = {
        "impl": "tests.fixtures.custom_plugins.CustomResponseRepairPolicy",
        "config": {"result": "from repair policy"},
    }
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    try:
        result = await runtime.run(
            agent_id="assistant",
            session_id="claude-code-pattern-repair-seam",
            input_text="hello",
        )
    finally:
        await runtime.close()

    assert result == "from repair policy"
    assert client.calls == 2
