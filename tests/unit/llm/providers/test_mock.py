from __future__ import annotations

import json

import pytest

from openagents.llm.base import LLMChunk, LLMResponse, LLMToolCall
from openagents.llm.providers.mock import MockClient, MockLLMClient


@pytest.mark.asyncio
async def test_generate_returns_populated_llm_response():
    mock = MockLLMClient(model="mock-1")
    result = await mock.generate(messages=[{"role": "user", "content": "INPUT: hello"}])

    assert isinstance(result, LLMResponse)
    assert result.provider == "mock"
    assert result.model == "mock-1"
    assert result.output_text  # non-empty
    assert result.content == [{"type": "text", "text": result.output_text}]
    assert result.usage is not None
    assert result.usage.total_tokens > 0
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.response_id.startswith("mock-")
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_generate_tool_directive_produces_tool_call():
    mock = MockLLMClient(model="mock-1")
    tools = [
        {
            "name": "lookup",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    result = await mock.generate(
        messages=[{"role": "user", "content": "INPUT: /tool lookup foo"}],
        tools=tools,
    )
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert isinstance(call, LLMToolCall)
    assert call.name == "lookup"
    assert call.arguments == {"query": "foo"}
    assert call.type == "tool_use"
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_generate_tool_directive_without_matching_tool_emits_no_tool_call():
    mock = MockLLMClient(model="mock-1")
    # No tools provided AND /tool directive — the directive text still lands in
    # output_text but no LLMToolCall is produced.
    result = await mock.generate(
        messages=[{"role": "user", "content": "INPUT: /tool missing abc"}],
    )
    assert result.tool_calls == []
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_generate_tool_directive_with_nonmatching_tool_emits_no_tool_call():
    mock = MockLLMClient(model="mock-1")
    tools = [
        {"name": "search", "input_schema": {"type": "object", "properties": {}}},
    ]
    result = await mock.generate(
        messages=[{"role": "user", "content": "INPUT: /tool notreal x"}],
        tools=tools,
    )
    # directive asks for tool "notreal" but only "search" is available
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_generate_response_format_json_object_populates_structured_output():
    mock = MockLLMClient(model="mock-1")
    result = await mock.generate(
        messages=[{"role": "user", "content": "INPUT: hello"}],
        response_format={"type": "json_object"},
    )
    # complete() returns JSON already; structured_output should be the parsed dict
    assert isinstance(result.structured_output, dict)
    assert result.structured_output.get("type") == "final"


@pytest.mark.asyncio
async def test_generate_response_format_invalid_json_returns_none_structured():
    """If complete() output is NOT valid JSON (shouldn't happen today but
    defensive), structured_output stays None."""
    mock = MockLLMClient(model="mock-1")

    # Monkey-patch complete to return plain text
    async def _plain_text(**kwargs):
        return "not a json payload"

    mock.complete = _plain_text  # type: ignore[assignment]
    result = await mock.generate(
        messages=[{"role": "user", "content": "INPUT: test"}],
        response_format={"type": "json_object"},
    )
    assert result.structured_output is None


@pytest.mark.asyncio
async def test_generate_is_deterministic_across_calls():
    mock = MockLLMClient(model="mock-1")
    msgs = [{"role": "user", "content": "INPUT: same prompt"}]

    first = await mock.generate(messages=msgs)
    second = await mock.generate(messages=msgs)

    assert first.output_text == second.output_text
    assert first.usage.input_tokens == second.usage.input_tokens
    assert first.usage.output_tokens == second.usage.output_tokens
    assert first.response_id == second.response_id


@pytest.mark.asyncio
async def test_complete_stream_emits_text_delta_then_message_stop():
    mock = MockLLMClient(model="mock-1")
    chunks: list[LLMChunk] = []
    async for chunk in mock.complete_stream(messages=[{"role": "user", "content": "INPUT: hi"}]):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0].type == "content_block_delta"
    assert chunks[0].delta["type"] == "text_delta"
    assert chunks[0].delta["text"]  # non-empty
    assert chunks[1].type == "message_stop"
    assert chunks[1].usage is not None


@pytest.mark.asyncio
async def test_complete_stream_deltas_reconstruct_non_streaming_output():
    mock = MockLLMClient(model="mock-1")
    msgs = [{"role": "user", "content": "INPUT: reconstruct"}]

    non_stream = await mock.generate(messages=msgs)

    streamed_text = ""
    async for chunk in mock.complete_stream(messages=msgs):
        if chunk.type == "content_block_delta" and chunk.delta:
            streamed_text += chunk.delta["text"]

    assert streamed_text == non_stream.output_text


@pytest.mark.asyncio
async def test_get_last_response_after_stream_matches_shape():
    mock = MockLLMClient(model="mock-1")
    msgs = [{"role": "user", "content": "INPUT: hi"}]

    async for _ in mock.complete_stream(messages=msgs):
        pass

    last = mock.get_last_response()
    assert last is not None
    # The stream's generate() call populated _last_response
    assert last.usage is not None
    assert last.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_complete_unchanged_for_representative_prompt():
    """complete() must keep its legacy JSON-shape output for existing callers."""
    mock = MockLLMClient(model="mock-1")
    text = await mock.complete(messages=[{"role": "user", "content": "INPUT: hi"}])
    parsed = json.loads(text)
    assert parsed["type"] == "final"
    assert "Echo: hi" in parsed["content"]


@pytest.mark.asyncio
async def test_complete_with_tool_directive_preserves_legacy_shape():
    mock = MockLLMClient(model="mock-1")
    text = await mock.complete(messages=[{"role": "user", "content": "INPUT: /tool lookup foo"}])
    parsed = json.loads(text)
    assert parsed["type"] == "tool_call"
    assert parsed["tool"] == "lookup"
    assert parsed["params"]["query"] == "foo"


@pytest.mark.asyncio
async def test_complete_handles_bare_tool_directive():
    mock = MockLLMClient(model="mock-1")
    text = await mock.complete(messages=[{"role": "user", "content": "INPUT: /tool"}])
    parsed = json.loads(text)
    assert parsed["type"] == "final"
    assert "Usage" in parsed["content"]


def test_mockclient_is_alias_for_mockllmclient():
    assert MockClient is MockLLMClient


def test_parse_prompt_counts_user_history_markers():
    mock = MockLLMClient()
    text = "HISTORY:\nUser: first\nAssistant: reply\nUser: second\nINPUT: now\n"
    values = mock._parse_prompt(text)
    assert values["input"] == "now"
    assert values["history_count"] == 2


def test_parse_prompt_reads_explicit_history_count():
    mock = MockLLMClient()
    text = "HISTORY_COUNT: 7\nINPUT: hello\n"
    values = mock._parse_prompt(text)
    assert values["input"] == "hello"
    assert values["history_count"] == 7


def test_parse_prompt_invalid_history_count_defaults_to_zero():
    mock = MockLLMClient()
    text = "HISTORY_COUNT: not-a-number\nINPUT: hi\n"
    values = mock._parse_prompt(text)
    assert values["history_count"] == 0


def test_parse_prompt_empty_returns_defaults():
    mock = MockLLMClient()
    values = mock._parse_prompt("")
    assert values["input"] == ""
    assert values["history_count"] == 0


@pytest.mark.asyncio
async def test_generate_with_no_user_message_still_returns_response():
    mock = MockLLMClient(model="mock-1")
    result = await mock.generate(messages=[{"role": "assistant", "content": "hi"}])
    # no user message → empty input text → echo of ""
    assert result.output_text
    assert result.usage is not None
