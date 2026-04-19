from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.app.plugins import OutlinePattern
from examples.pptx_generator.state import SlideOutline


def _make_ctx(llm_return: str):
    return SimpleNamespace(
        input_text="",
        state={
            "intent": {"slide_count_hint": 3, "topic": "t", "required_sections": []},
            "research": {"key_facts": []},
        },
        memory_view={},
        tool_results=[],
        assembly_metadata={},
        llm_client=SimpleNamespace(complete=AsyncMock(return_value=llm_return)),
    )


@pytest.mark.asyncio
async def test_outline_generates_valid_slides():
    payload = json.dumps({
        "slides": [
            {"index": 1, "type": "cover", "title": "T", "key_points": [], "sources_cited": []},
            {"index": 2, "type": "content", "title": "Why", "key_points": ["p1"], "sources_cited": []},
            {"index": 3, "type": "closing", "title": "Thanks", "key_points": [], "sources_cited": []},
        ]
    })
    pattern = OutlinePattern(config={})
    pattern.context = _make_ctx(payload)
    result = await pattern.execute()
    assert isinstance(result, SlideOutline)
    assert len(result.slides) == 3
    assert pattern.context.state["outline"]["slides"][0]["type"] == "cover"


@pytest.mark.asyncio
async def test_outline_invalid_retries():
    calls = []

    async def complete(*, messages, **kwargs):
        calls.append(messages)
        if len(calls) < 2:
            return "garbage"
        return json.dumps({
            "slides": [
                {"index": 1, "type": "cover", "title": "T", "key_points": [], "sources_cited": []},
            ]
        })

    pattern = OutlinePattern(config={"max_steps": 3})
    ctx = _make_ctx("")
    ctx.llm_client.complete = complete  # override
    pattern.context = ctx
    result = await pattern.execute()
    assert len(result.slides) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_outline_exhaust_raises():
    pattern = OutlinePattern(config={"max_steps": 2})
    pattern.context = _make_ctx("still garbage")
    with pytest.raises(RuntimeError, match="exhausted"):
        await pattern.execute()
