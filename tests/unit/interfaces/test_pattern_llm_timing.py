from __future__ import annotations

import pytest

from openagents.interfaces.diagnostics import LLMCallMetrics
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunUsage
from openagents.llm.base import LLMResponse, LLMUsage


class _FakeEventBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


class _FakeClient:
    def __init__(self, responses, *, raise_on_call=None):
        self._responses = list(responses)
        self._raise_on_call = raise_on_call

    async def generate(self, **_kwargs):
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._responses.pop(0)


class _TestPattern(PatternPlugin):
    async def execute(self):  # pragma: no cover - not invoked
        return None


@pytest.mark.asyncio
async def test_llm_succeeded_payload_contains_metrics():
    usage = LLMUsage(
        input_tokens=10,
        output_tokens=20,
        metadata={"cache_read_input_tokens": 4},
    )
    resp = LLMResponse(output_text="ok", usage=usage)
    bus = _FakeEventBus()

    pattern = _TestPattern(config={})
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={},
        llm_client=_FakeClient([resp]),
        llm_options=None,
        event_bus=bus,
        usage=RunUsage(),
    )

    await pattern.call_llm(messages=[{"role": "user", "content": "hi"}], model="test-model")

    succeeded_events = [p for n, p in bus.events if n == "llm.succeeded"]
    assert len(succeeded_events) == 1
    payload = succeeded_events[0]
    assert payload.get("model") == "test-model"
    assert "_metrics" in payload
    metrics = payload["_metrics"]
    assert isinstance(metrics, LLMCallMetrics)
    assert metrics.model == "test-model"
    assert metrics.latency_ms >= 0.0
    assert metrics.input_tokens == 10
    assert metrics.output_tokens == 20
    assert metrics.cached_tokens == 4
    assert metrics.ttft_ms is None


@pytest.mark.asyncio
async def test_llm_failed_payload_contains_metrics_on_generate_error():
    bus = _FakeEventBus()
    boom = RuntimeError("upstream 500")

    pattern = _TestPattern(config={})
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={},
        llm_client=_FakeClient([], raise_on_call=boom),
        llm_options=None,
        event_bus=bus,
        usage=RunUsage(),
    )

    with pytest.raises(RuntimeError):
        await pattern.call_llm(messages=[{"role": "user", "content": "hi"}], model="err-model")

    failed_events = [p for n, p in bus.events if n == "llm.failed"]
    assert len(failed_events) == 1
    payload = failed_events[0]
    assert payload.get("model") == "err-model"
    assert "_metrics" in payload
    metrics = payload["_metrics"]
    assert isinstance(metrics, LLMCallMetrics)
    assert metrics.error == "upstream 500"
    assert metrics.model == "err-model"
    assert metrics.latency_ms >= 0.0
