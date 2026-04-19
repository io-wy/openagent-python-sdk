import pytest

from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunUsage
from openagents.llm.base import LLMResponse, LLMUsage


class _FakeEventBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def generate(self, **_kwargs):
        return self._responses.pop(0)


class _TestPattern(PatternPlugin):
    async def execute(self):  # pragma: no cover - abstract placeholder
        return None


@pytest.mark.asyncio
async def test_pattern_accumulates_cost_and_cached_tokens():
    usage1 = LLMUsage(
        input_tokens=100,
        output_tokens=50,
        metadata={"cost_usd": 0.01, "cost_breakdown": {"input": 0.004, "output": 0.006}},
    )
    usage2 = LLMUsage(
        input_tokens=200,
        output_tokens=100,
        metadata={"cost_usd": 0.02, "cost_breakdown": {"input": 0.008, "output": 0.012}},
    )
    resp1 = LLMResponse(output_text="a", usage=usage1)
    resp2 = LLMResponse(output_text="b", usage=usage2)

    pattern = _TestPattern(config={}, capabilities=set())
    await pattern.setup(
        agent_id="a", session_id="s", input_text="hi",
        state={}, tools={},
        llm_client=_FakeClient([resp1, resp2]), llm_options=None,
        event_bus=_FakeEventBus(),
        usage=RunUsage(),
    )

    await pattern.call_llm(messages=[{"role": "user", "content": "hi"}])
    await pattern.call_llm(messages=[{"role": "user", "content": "hi2"}])

    assert pattern.context.usage.llm_calls == 2
    assert pattern.context.usage.cost_usd == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_cost_goes_none_sticky_when_any_call_has_none():
    usage1 = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": 0.01})
    usage2 = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": None})
    usage3 = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": 0.01})
    resps = [LLMResponse(output_text=x, usage=u) for x, u in [("a", usage1), ("b", usage2), ("c", usage3)]]
    pattern = _TestPattern(config={}, capabilities=set())
    await pattern.setup(
        agent_id="a", session_id="s", input_text="hi",
        state={}, tools={},
        llm_client=_FakeClient(resps), llm_options=None,
        event_bus=_FakeEventBus(),
        usage=RunUsage(),
    )
    for _ in range(3):
        await pattern.call_llm(messages=[{"role": "user", "content": "q"}])
    assert pattern.context.usage.cost_usd is None
    assert pattern.context.scratch.get("__cost_unavailable__") is True
