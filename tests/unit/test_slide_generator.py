# tests/unit/test_slide_generator.py
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.app.plugins import SlideGenPattern
from examples.pptx_generator.state import SlideIR


def _spec(index, type_):
    return {"index": index, "type": type_, "title": "T", "key_points": ["p"], "sources_cited": []}


def _make_ctx(*, spec: dict, llm_return: str):
    payload = json.dumps({"target_spec": spec, "theme": {}})
    return SimpleNamespace(
        input_text=payload,
        state={},
        memory_view={},
        tool_results=[],
        assembly_metadata={},
        llm_client=SimpleNamespace(complete=AsyncMock(return_value=llm_return)),
    )


@pytest.mark.asyncio
async def test_generates_valid_cover():
    llm_return = json.dumps({"title": "T", "subtitle": "sub", "author": "me"})
    pattern = SlideGenPattern(config={"max_retries": 2})
    pattern.context = _make_ctx(spec=_spec(1, "cover"), llm_return=llm_return)
    result = await pattern.execute()
    assert isinstance(result, SlideIR)
    assert result.type == "cover"
    assert result.slots["title"] == "T"
    assert result.index == 1


@pytest.mark.asyncio
async def test_falls_back_to_freeform_after_retries():
    pattern = SlideGenPattern(config={"max_retries": 1, "allow_freeform_fallback": True})
    pattern.context = _make_ctx(spec=_spec(2, "content"), llm_return="garbage")
    result = await pattern.execute()
    assert result.type == "freeform"
    assert result.freeform_js
    assert result.index == 2


@pytest.mark.asyncio
async def test_raises_when_freeform_disabled():
    pattern = SlideGenPattern(config={"max_retries": 1, "allow_freeform_fallback": False})
    pattern.context = _make_ctx(spec=_spec(3, "content"), llm_return="garbage")
    with pytest.raises(RuntimeError):
        await pattern.execute()


@pytest.mark.asyncio
async def test_unknown_type_falls_back_to_freeform():
    pattern = SlideGenPattern(config={"max_retries": 1})
    pattern.context = _make_ctx(
        spec={"index": 4, "type": "unknown", "title": "X", "key_points": [], "sources_cited": []}, llm_return=""
    )
    result = await pattern.execute()
    assert result.type == "freeform"
