"""Tests for tool-side ModelRetryError handling in pattern.call_tool."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import ModelRetryError, PermanentToolError
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunBudget, RunRequest, RunUsage
from openagents.interfaces.tool import ToolPlugin


class _FailingTool(ToolPlugin):
    def __init__(self, config=None):
        super().__init__(config=config or {}, capabilities=set())
        self.calls = 0

    async def invoke(self, params, context):
        self.calls += 1
        raise ModelRetryError("missing field X")


class _Bus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


class _TestPattern(PatternPlugin):
    async def execute(self):  # pragma: no cover
        return None


@pytest.mark.asyncio
async def test_call_tool_retry_emits_event_and_transcript_correction_until_escalation():
    """First N calls raise ModelRetryError → emit tool.retry_requested and add
    a transcript correction. After limit+1 total calls, escalate to
    PermanentToolError."""
    pattern = _TestPattern(config={}, capabilities=set())
    failing = _FailingTool()
    request = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        budget=RunBudget(max_validation_retries=2),
    )
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={"bad": failing},
        llm_client=None,
        llm_options=None,
        event_bus=_Bus(),
        usage=RunUsage(),
        run_request=request,
    )

    # With limit=2, the 1st and 2nd calls get ModelRetryError re-raised (so pattern
    # can decide what to do). The 3rd call (counter becomes 3, 3 > 2) escalates.
    for attempt in (1, 2):
        with pytest.raises(ModelRetryError):
            await pattern.call_tool("bad", {"x": 1})

    with pytest.raises(PermanentToolError):
        await pattern.call_tool("bad", {"x": 1})

    assert failing.calls == 3

    events = [name for name, _ in pattern.context.event_bus.events]
    assert events.count("tool.retry_requested") == 2
    assert events.count("tool.failed") == 1

    # Transcript has the correction messages for each retry.
    sys_messages = [m for m in pattern.context.transcript if m.get("role") == "system"]
    assert len(sys_messages) == 2


@pytest.mark.asyncio
async def test_call_tool_resets_retry_counter_on_success():
    """A successful call between failures should reset the per-tool counter."""

    class _SometimesFailing(ToolPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())
            self.calls = 0

        async def invoke(self, params, context):
            self.calls += 1
            if self.calls == 1:
                raise ModelRetryError("first-call fail")
            return {"ok": True}

    pattern = _TestPattern(config={}, capabilities=set())
    tool = _SometimesFailing()
    request = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        budget=RunBudget(max_validation_retries=3),
    )
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={"t": tool},
        llm_client=None,
        llm_options=None,
        event_bus=_Bus(),
        usage=RunUsage(),
        run_request=request,
    )
    with pytest.raises(ModelRetryError):
        await pattern.call_tool("t", {})
    # Second call succeeds.
    result = await pattern.call_tool("t", {})
    assert result == {"ok": True}
    counts = pattern.context.scratch.get("__tool_retry_counts__") or {}
    assert "t" not in counts
