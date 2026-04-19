from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.app.plugins import IntentAnalystPattern
from examples.pptx_generator.state import IntentReport


def _make_context(llm_complete_return: str, user_prompt: str = "Make me a 6-slide pitch."):
    ctx = SimpleNamespace(
        input_text=user_prompt,
        memory_view={"user_goals": [], "user_feedback": []},
        tool_results=[],
        state={},
        assembly_metadata={},
        llm_client=SimpleNamespace(
            complete=AsyncMock(return_value=llm_complete_return)
        ),
    )
    return ctx


@pytest.mark.asyncio
async def test_intent_produces_valid_report():
    payload = json.dumps({
        "topic": "AI backup tool",
        "audience": "VC investors",
        "purpose": "pitch",
        "tone": "energetic",
        "slide_count_hint": 6,
        "required_sections": ["problem", "solution", "market", "ask"],
        "visuals_hint": ["architecture diagram"],
        "research_queries": ["enterprise backup market 2026"],
        "language": "en",
    })
    pattern = IntentAnalystPattern(config={})
    pattern.context = _make_context(payload)
    result = await pattern.execute()
    assert isinstance(result, IntentReport)
    assert result.purpose == "pitch"
    assert result.slide_count_hint == 6


@pytest.mark.asyncio
async def test_intent_invalid_json_retries_once():
    call_log = []

    async def complete(*, messages, **kwargs):
        call_log.append(messages)
        if len(call_log) == 1:
            return "not json"
        return json.dumps({
            "topic": "t", "audience": "a", "purpose": "pitch",
            "tone": "formal", "slide_count_hint": 5,
            "required_sections": [], "visuals_hint": [],
            "research_queries": [], "language": "zh",
        })

    pattern = IntentAnalystPattern(config={"max_steps": 3})
    pattern.context = SimpleNamespace(
        input_text="draft deck",
        memory_view={},
        tool_results=[],
        state={},
        assembly_metadata={},
        llm_client=SimpleNamespace(complete=complete),
    )
    result = await pattern.execute()
    assert isinstance(result, IntentReport)
    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_intent_exhaust_retries_raises():
    pattern = IntentAnalystPattern(config={"max_steps": 2})
    pattern.context = _make_context("still not json")
    with pytest.raises(RuntimeError, match="exhausted"):
        await pattern.execute()


@pytest.mark.asyncio
async def test_intent_handles_fenced_with_trailing_text():
    import json
    payload = json.dumps({
        "topic": "t", "audience": "a", "purpose": "pitch",
        "tone": "formal", "slide_count_hint": 5,
        "required_sections": [], "visuals_hint": [],
        "research_queries": [], "language": "zh",
    })
    fenced = f"```json\n{payload}\n```\n\n(additional commentary the LLM added)"
    pattern = IntentAnalystPattern(config={})
    pattern.context = _make_context(fenced)
    result = await pattern.execute()
    assert isinstance(result, IntentReport)
    assert result.purpose == "pitch"
