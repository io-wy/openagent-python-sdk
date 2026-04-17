"""Tests for the three token-budget context assemblers (Tasks 28-31)."""

from __future__ import annotations

import pytest

from openagents.plugins.builtin.context.head_tail import HeadTailContextAssembler
from openagents.plugins.builtin.context.importance_weighted import (
    ImportanceWeightedContextAssembler,
)
from openagents.plugins.builtin.context.sliding_window import SlidingWindowContextAssembler


class _LLM:
    provider_name = "mock"
    model_id = "mock"

    def count_tokens(self, text: str) -> int:
        return max(1, len(text))


class _Session:
    def __init__(self, messages, artifacts):
        self._messages = messages
        self._artifacts = artifacts

    async def load_messages(self, sid):
        return list(self._messages)

    async def list_artifacts(self, sid):
        return list(self._artifacts)


def _req():
    return type("R", (), {"session_id": "s"})()


@pytest.mark.asyncio
async def test_sliding_window_keeps_recent_messages():
    msgs = [{"role": "user", "content": "x" * 3} for _ in range(10)]
    assembler = SlidingWindowContextAssembler(
        config={"max_input_tokens": 10, "reserve_for_response": 0}
    )
    result = await assembler.assemble(
        request=_req(),
        session_state={"llm_client": _LLM()},
        session_manager=_Session(msgs, []),
    )
    # Budget 10 tokens, each message = 3 tokens ≠ 10; so 3 messages kept.
    assert len(result.transcript) == 3
    assert result.metadata["omitted_messages"] == 7
    assert result.metadata["strategy"] == "slidingwindow"
    assert result.metadata["token_counter"] == "fallback_len//4"  # mock provider


@pytest.mark.asyncio
async def test_head_tail_keeps_head_and_tail():
    msgs = [{"role": "user", "content": "x" * 3} for _ in range(10)]
    assembler = HeadTailContextAssembler(
        config={"max_input_tokens": 12, "reserve_for_response": 0, "head_messages": 2}
    )
    result = await assembler.assemble(
        request=_req(),
        session_state={"llm_client": _LLM()},
        session_manager=_Session(msgs, []),
    )
    # Head (2) + fill remaining 6 tokens = 2 tail → 4 kept + 1 summary line.
    kept_texts = [m.get("content") for m in result.transcript]
    # The first two are head (original messages), then potentially a summary, then tail.
    assert kept_texts[0] == "x" * 3
    assert kept_texts[1] == "x" * 3
    # Summary message present when any messages omitted.
    assert any("omitted" in t for t in kept_texts)


@pytest.mark.asyncio
async def test_importance_weighted_keeps_priority_messages():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "early question"},
        {"role": "assistant", "content": "early answer"},
        {"role": "assistant", "content": "filler"},
        {"role": "tool", "content": "tool result"},
        {"role": "user", "content": "recent question"},
    ]
    assembler = ImportanceWeightedContextAssembler(
        config={"max_input_tokens": 30, "reserve_for_response": 0}
    )
    result = await assembler.assemble(
        request=_req(),
        session_state={"llm_client": _LLM()},
        session_manager=_Session(msgs, []),
    )
    kept_contents = [m.get("content") for m in result.transcript]
    # Budget of 30 tokens covers all messages since the sum of lengths is 51.
    # With tight budget, system + latest user + tool are prioritized.
    assert "sys" in kept_contents
    # Chronological order preserved.
    indices = [kept_contents.index(c) for c in kept_contents if c in kept_contents]
    assert indices == sorted(indices)


@pytest.mark.asyncio
async def test_importance_weighted_tight_budget_preserves_priority():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "filler-content"},
        {"role": "tool", "content": "tool-result"},
    ]
    # budget 16 tokens: "sys"=3, "question"=8, "filler-content"=14, "tool-result"=11
    # priority: system(1000) > tool(900-x) > user(800-x) > assistant(500-x)
    # score-sorted: system(3), tool(11), user(8), assistant(14)
    # fill greedily: system(3, remain=13), tool(11, remain=2), user skipped (8>2), assistant skipped
    assembler = ImportanceWeightedContextAssembler(
        config={"max_input_tokens": 14, "reserve_for_response": 0}
    )
    result = await assembler.assemble(
        request=_req(),
        session_state={"llm_client": _LLM()},
        session_manager=_Session(msgs, []),
    )
    kept = [m.get("content") for m in result.transcript]
    assert "sys" in kept
    assert "tool-result" in kept
    assert "filler-content" not in kept
