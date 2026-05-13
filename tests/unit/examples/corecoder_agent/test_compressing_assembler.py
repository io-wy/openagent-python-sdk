"""Tests for ``CompressingContextAssembler`` — Layer 1/2/3 triggers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from examples.corecoder_agent.app.context import (
    CompressingContextAssembler,
    _content_to_text,
    _heuristic_summary,
    _snip_text,
)


class _FakeSessionManager:
    def __init__(self, transcript: list[dict[str, Any]], artifacts: list[Any]) -> None:
        self._transcript = transcript
        self._artifacts = artifacts

    async def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._transcript)

    async def list_artifacts(self, session_id: str) -> list[Any]:
        return list(self._artifacts)


def _msg(role: str, content: Any) -> dict[str, Any]:
    return {"role": role, "content": content}


# ---- helper functions --------------------------------------------------


def test_snip_text_short_input_unchanged() -> None:
    text, saved = _snip_text("hello", head=100, tail=100)
    assert text == "hello"
    assert saved == 0


def test_snip_text_long_input_marker_inserted() -> None:
    long = "a" * 5000
    text, saved = _snip_text(long, head=100, tail=50)
    assert "snipped" in text
    assert text.startswith("a" * 100)
    assert text.endswith("a" * 50)
    assert saved == 5000 - 100 - 50


def test_content_to_text_handles_blocks() -> None:
    blocks = [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
        {"type": "tool_result", "content": "out"},
    ]
    rendered = _content_to_text(blocks)
    assert "hi" in rendered
    assert "tool_use" in rendered
    assert "out" in rendered


def test_heuristic_summary_caps_words() -> None:
    text = "alpha beta gamma\n" * 50
    summary = _heuristic_summary(text, max_words=10)
    assert len(summary.split()) <= 11  # 10 words + maybe trailing "..."


# ---- layer triggers -----------------------------------------------------


@pytest.mark.asyncio
async def test_layer1_snip_fires_on_huge_tool_result() -> None:
    big_payload = "x" * 6000
    transcript = [
        _msg("user", "task"),
        _msg("assistant", [{"type": "text", "text": "running"}]),
        _msg(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": big_payload,
                }
            ],
        ),
    ]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 1500,
            "reserve_for_response": 100,
            "snip_threshold": 0.5,
            "summarize_threshold": 0.99,
            "hard_collapse_threshold": 0.99,
            "tool_output_max_bytes": 500,
            "tool_output_keep_head": 200,
            "tool_output_keep_tail": 100,
        }
    )
    request = SimpleNamespace(session_id="s1")
    sm = _FakeSessionManager(transcript, [])
    result = await assembler.assemble(
        request=request, session_state={}, session_manager=sm
    )
    assert "snip" in result.metadata["layers_fired"]
    assert result.metadata["tokens_after"] < result.metadata["tokens_before"]


@pytest.mark.asyncio
async def test_layer2_summarize_fires_with_no_llm_fallback() -> None:
    transcript = [
        _msg("user", "initial task"),
        _msg("assistant", "ok"),
    ]
    # 30 mid messages > keep_recent (5) so something gets summarized.
    for i in range(30):
        transcript.append(_msg("assistant", f"step {i}: doing things " * 50))
    transcript.append(_msg("user", "final question"))

    assembler = CompressingContextAssembler(
        config={
            # Budget large enough that Layer 2 (summarize) brings us well below
            # the Layer 3 (hard_collapse) threshold; we want to isolate Layer 2.
            "max_input_tokens": 4000,
            "reserve_for_response": 200,
            "snip_threshold": 0.99,  # disable layer 1 for this test
            "summarize_threshold": 0.5,
            "hard_collapse_threshold": 0.99,
            "keep_recent_messages_for_summary": 5,
            "keep_first_messages": 2,
        }
    )
    request = SimpleNamespace(session_id="s1")
    sm = _FakeSessionManager(transcript, [])
    result = await assembler.assemble(
        request=request, session_state={"llm_client": None}, session_manager=sm
    )
    assert "summarize" in result.metadata["layers_fired"]
    # First two messages preserved, summary inserted, last 5 preserved.
    transcript_out = result.transcript
    assert transcript_out[0]["content"] == "initial task"
    assert any(
        isinstance(m.get("content"), str) and "context compression" in m["content"]
        for m in transcript_out
    )


@pytest.mark.asyncio
async def test_layer3_hard_collapse_fires() -> None:
    # Build a transcript big enough that layer 3 must fire even after layer 1+2.
    transcript = [_msg("user", "first message"), _msg("assistant", "ack")]
    bulk = "y" * 4000
    for _ in range(30):
        transcript.append(_msg("assistant", bulk))
    transcript.append(_msg("user", "newest"))

    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 200,
            "reserve_for_response": 50,
            "snip_threshold": 0.5,
            "summarize_threshold": 0.5,
            "hard_collapse_threshold": 0.5,
            "tool_output_max_bytes": 200,
            "tool_output_keep_head": 50,
            "tool_output_keep_tail": 50,
            "keep_recent_messages_for_summary": 5,
            "keep_first_messages": 2,
            "keep_last_messages_on_collapse": 3,
        }
    )
    request = SimpleNamespace(session_id="s1")
    sm = _FakeSessionManager(transcript, [])
    result = await assembler.assemble(
        request=request, session_state={"llm_client": None}, session_manager=sm
    )
    assert "hard_collapse" in result.metadata["layers_fired"]
    assert any(
        isinstance(m.get("content"), str) and "hard-collapse" in m["content"]
        for m in result.transcript
    )


@pytest.mark.asyncio
async def test_no_layers_fire_below_thresholds() -> None:
    transcript = [_msg("user", "tiny question"), _msg("assistant", "tiny answer")]
    assembler = CompressingContextAssembler(
        config={
            "max_input_tokens": 4000,
            "reserve_for_response": 1000,
        }
    )
    request = SimpleNamespace(session_id="s1")
    sm = _FakeSessionManager(transcript, [])
    result = await assembler.assemble(
        request=request, session_state={}, session_manager=sm
    )
    assert result.metadata["layers_fired"] == []
    assert result.transcript == transcript
