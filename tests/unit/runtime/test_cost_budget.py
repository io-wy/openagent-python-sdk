import pytest

from openagents.errors.exceptions import BudgetExhausted
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunBudget, RunRequest, RunUsage
from openagents.llm.base import LLMResponse, LLMUsage


class _FakeEventBus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


class _FakeClient:
    """A fake provider client for budget-enforcement tests."""

    def __init__(self, responses, price_per_mtok_input=3.0, price_per_mtok_output=15.0):
        self._responses = list(responses)
        self.price_per_mtok_input = price_per_mtok_input
        self.price_per_mtok_output = price_per_mtok_output
        self.price_per_mtok_cached_read = None
        self.price_per_mtok_cached_write = None
        self.provider_name = "fake"
        self.model_id = "fake-1"

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def generate(self, **_kwargs):
        return self._responses.pop(0)


class _TestPattern(PatternPlugin):
    async def execute(self):  # pragma: no cover
        return None


@pytest.mark.asyncio
async def test_cost_budget_post_call_exceeded_raises():
    # Post-call: after one call of cost 0.02, budget=0.01 is exceeded.
    usage = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": 0.02})
    response = LLMResponse(output_text="x", usage=usage)
    pattern = _TestPattern(config={})
    request = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        budget=RunBudget(max_cost_usd=0.01),
    )
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={},
        llm_client=_FakeClient([response]),
        llm_options=None,
        event_bus=_FakeEventBus(),
        usage=RunUsage(),
        run_request=request,
    )
    with pytest.raises(BudgetExhausted) as exc_info:
        await pattern.call_llm(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.kind == "cost"
    assert exc_info.value.limit == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_cost_budget_skipped_when_cost_unavailable_emits_event_once():
    # Provider has no prices; cost_usd stays None; no budget enforcement, but event is emitted once.
    usage = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": None})
    responses = [LLMResponse(output_text=c, usage=usage) for c in "abc"]
    pattern = _TestPattern(config={})
    request = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        budget=RunBudget(max_cost_usd=1.0),
    )
    bus = _FakeEventBus()
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={},
        llm_client=_FakeClient(responses, price_per_mtok_input=None, price_per_mtok_output=None),
        llm_options=None,
        event_bus=bus,
        usage=RunUsage(),
        run_request=request,
    )
    for _ in range(3):
        await pattern.call_llm(messages=[{"role": "user", "content": "q"}])
    # All three calls succeeded; cost_skipped event emitted exactly once.
    skipped = [e for e in bus.events if e[0] == "budget.cost_skipped"]
    assert len(skipped) == 1


@pytest.mark.asyncio
async def test_no_cost_budget_leaves_calls_alone():
    # No max_cost_usd → no enforcement, no skipped event.
    usage = LLMUsage(input_tokens=100, output_tokens=50, metadata={"cost_usd": 0.02})
    response = LLMResponse(output_text="x", usage=usage)
    pattern = _TestPattern(config={})
    request = RunRequest(agent_id="a", session_id="s", input_text="hi")  # no budget
    bus = _FakeEventBus()
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="hi",
        state={},
        tools={},
        llm_client=_FakeClient([response]),
        llm_options=None,
        event_bus=bus,
        usage=RunUsage(),
        run_request=request,
    )
    await pattern.call_llm(messages=[{"role": "user", "content": "hi"}])
    assert not any(e[0].startswith("budget.") for e in bus.events)
