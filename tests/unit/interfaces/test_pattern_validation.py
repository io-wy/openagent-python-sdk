import pytest

from openagents.interfaces.runtime import RunUsage
from openagents.plugins.builtin.pattern.react import ReActPattern


class _Bus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


@pytest.mark.asyncio
async def test_react_injects_correction_message_when_scratch_has_error():
    pattern = ReActPattern(config={})
    await pattern.setup(
        agent_id="a", session_id="s", input_text="hi",
        state={}, tools={}, llm_client=None, llm_options=None,
        event_bus=_Bus(), usage=RunUsage(),
    )
    pattern.context.scratch["last_validation_error"] = {
        "attempt": 1,
        "message": "name is required",
        "expected_schema": {"type": "object"},
    }
    pattern._inject_validation_correction()
    assert any(
        m.get("role") == "system" and "validation" in m.get("content", "").lower()
        for m in pattern.context.transcript
    )
    assert "last_validation_error" not in pattern.context.scratch


@pytest.mark.asyncio
async def test_plan_execute_injects_correction():
    from openagents.plugins.builtin.pattern.plan_execute import PlanExecutePattern

    pattern = PlanExecutePattern(config={})
    await pattern.setup(
        agent_id="a", session_id="s", input_text="hi",
        state={}, tools={}, llm_client=None, llm_options=None,
        event_bus=_Bus(), usage=RunUsage(),
    )
    pattern.context.scratch["last_validation_error"] = {
        "attempt": 1, "message": "missing field", "expected_schema": {}
    }
    pattern._inject_validation_correction()
    assert any(m.get("role") == "system" for m in pattern.context.transcript)


@pytest.mark.asyncio
async def test_reflexion_injects_correction():
    from openagents.plugins.builtin.pattern.reflexion import ReflexionPattern

    pattern = ReflexionPattern(config={})
    await pattern.setup(
        agent_id="a", session_id="s", input_text="hi",
        state={}, tools={}, llm_client=None, llm_options=None,
        event_bus=_Bus(), usage=RunUsage(),
    )
    pattern.context.scratch["last_validation_error"] = {
        "attempt": 1, "message": "bad schema", "expected_schema": {}
    }
    pattern._inject_validation_correction()
    assert any(m.get("role") == "system" for m in pattern.context.transcript)
