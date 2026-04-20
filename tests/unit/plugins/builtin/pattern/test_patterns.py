from __future__ import annotations

import json

import pytest

from openagents.interfaces.events import RuntimeEvent
from openagents.interfaces.runtime import RunRequest, RunUsage
from openagents.llm.base import LLMClient, LLMResponse
from openagents.plugins.builtin.pattern.plan_execute import PlanExecutePattern
from openagents.plugins.builtin.pattern.react import ReActPattern
from openagents.plugins.builtin.pattern.reflexion import ReflexionPattern


class _SequenceLLM(LLMClient):
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        *,
        messages,
        model=None,
        temperature=None,
        max_tokens=None,
        tools=None,
        tool_choice=None,
        response_format=None,
    ):
        _ = (messages, model, temperature, max_tokens, tools, tool_choice, response_format)
        if not self._responses:
            raise RuntimeError("No more fake responses")
        return LLMResponse(output_text=self._responses.pop(0))


class _EventBus:
    def __init__(self) -> None:
        self.history: list[RuntimeEvent] = []

    async def emit(self, event_name: str, **payload):
        event = RuntimeEvent(name=event_name, payload=payload)
        self.history.append(event)
        return event


class _EchoTool:
    async def invoke(self, params, context):
        return {"echo": params}

    async def fallback(self, error, params, context):
        raise AssertionError("fallback should not run")


async def _setup_pattern(pattern, *, llm_client, tools, input_text="solve task"):
    await pattern.setup(
        agent_id="assistant",
        session_id="s1",
        input_text=input_text,
        state={},
        tools=tools,
        llm_client=llm_client,
        llm_options={"model": "mock"},
        event_bus=_EventBus(),
        run_request=RunRequest(agent_id="assistant", session_id="s1", input_text=input_text),
        usage=RunUsage(),
        artifacts=[],
    )


@pytest.mark.asyncio
async def test_plan_execute_pattern_runs_plan_and_handles_non_llm_fallback():
    llm = _SequenceLLM(
        [
            json.dumps(
                {
                    "plan": [
                        {"action": "tool_call", "tool": "echo", "params": {"value": 1}},
                        {"type": "final", "content": "done"},
                    ]
                }
            )
        ]
    )
    pattern = PlanExecutePattern({"max_steps": 4, "step_timeout_ms": 1234})
    await _setup_pattern(pattern, llm_client=llm, tools={"echo": _EchoTool()})

    result = await pattern.execute()
    parsed = pattern._parse_llm_response('prefix {"type":"final","content":"embedded"} suffix')

    assert pattern._max_steps() == 4
    assert pattern._step_timeout_ms() == 1234
    assert result == "done"
    assert pattern.context.scratch["_plan"][0]["tool"] == "echo"
    assert pattern.context.tool_results == [{"tool_id": "echo", "result": {"echo": {"value": 1}}}]
    assert pattern.context.state["_runtime_last_output"] == "done"
    assert parsed == {"type": "final", "content": "embedded"}
    assert await pattern.react() == {"type": "final", "content": "Use execute() for PlanExecute pattern"}

    no_llm = PlanExecutePattern()
    await _setup_pattern(no_llm, llm_client=None, tools={})
    assert await no_llm.execute() == {"type": "final", "content": "PlanExecute requires LLM"}


@pytest.mark.asyncio
async def test_reflexion_pattern_executes_tool_then_finishes_after_reflection():
    llm = _SequenceLLM(
        [
            json.dumps({"type": "tool_call", "tool": "echo", "params": {"value": 7}}),
            json.dumps({"type": "final", "content": "reflected result"}),
        ]
    )
    pattern = ReflexionPattern({"max_steps": 4, "max_retries": 2})
    await _setup_pattern(pattern, llm_client=llm, tools={"echo": _EchoTool()}, input_text="inspect")

    result = await pattern.execute()

    assert result == "reflected result"
    assert pattern.context.state["_runtime_last_output"] == "reflected result"
    assert pattern.context.tool_results[0]["tool_id"] == "echo"
    assert [event.name for event in pattern.context.event_bus.history] == [
        "pattern.step_started",
        "llm.called",
        "usage.updated",
        "llm.succeeded",
        "pattern.step_finished",
        "tool.called",
        "tool.succeeded",
        "pattern.step_started",
        "llm.called",
        "usage.updated",
        "llm.succeeded",
        "pattern.step_finished",
    ]


@pytest.mark.asyncio
async def test_reflexion_pattern_handles_retry_limit_and_missing_llm():
    retry_llm = _SequenceLLM(
        [
            json.dumps({"type": "retry", "reason": "try again"}),
            json.dumps({"type": "retry", "reason": "still bad"}),
        ]
    )
    pattern = ReflexionPattern({"max_steps": 4, "max_retries": 2})
    await _setup_pattern(pattern, llm_client=retry_llm, tools={}, input_text="retry case")

    assert await pattern.execute() == "Max retries (2) reached"

    no_llm = ReflexionPattern({"max_steps": 1})
    await _setup_pattern(no_llm, llm_client=None, tools={}, input_text="no llm")
    with pytest.raises(RuntimeError, match=r"Pattern exceeded max_steps \(1\)"):
        await no_llm.execute()


@pytest.mark.asyncio
async def test_reflexion_pattern_helpers_and_remaining_error_branches():
    pattern = ReflexionPattern({"max_steps": 0, "step_timeout_ms": 0})
    await _setup_pattern(pattern, llm_client=None, tools={"echo": _EchoTool()}, input_text="helper check")

    pattern.context.memory_view["history"] = [
        {"input": "u1", "output": "a1"},
        {"input": "u2", "output": "a2"},
        "ignored",
    ]
    pattern.context.tool_results = [{"tool_id": "echo", "result": {"ok": True}}]

    assert pattern._max_steps() == 16
    assert pattern._step_timeout_ms() == 30000
    assert "User: u1" in pattern._format_history(pattern.context.memory_view["history"])
    assert "Recent tool results:" in pattern._reflection_prompt()
    assert "Available tools: echo" in pattern._action_prompt()
    assert pattern._parse_llm_response("plain text") == {"type": "final", "content": "plain text"}
    assert pattern._parse_llm_response('prefix {"type":"continue"} suffix') == {"type": "continue"}

    retry_llm = _SequenceLLM(
        [
            json.dumps(
                {
                    "type": "retry",
                    "adjusted_params": {"tool": "echo", "params": {"value": 9}},
                }
            )
        ]
    )
    retry_pattern = ReflexionPattern()
    await _setup_pattern(retry_pattern, llm_client=retry_llm, tools={"echo": _EchoTool()}, input_text="retry")
    retry_pattern.context.tool_results = [{"tool_id": "echo", "result": "old"}]
    assert await retry_pattern.react() == {"type": "tool_call", "tool": "echo", "params": {"value": 9}}

    class _BadActionType(ReflexionPattern):
        async def react(self):
            return "bad"

    bad_action = _BadActionType({"max_steps": 1})
    await _setup_pattern(bad_action, llm_client=None, tools={}, input_text="bad")
    with pytest.raises(TypeError, match="Pattern action must be dict"):
        await bad_action.execute()

    class _MissingTool(ReflexionPattern):
        async def react(self):
            return {"type": "tool_call", "params": {}}

    missing_tool = _MissingTool({"max_steps": 1})
    await _setup_pattern(missing_tool, llm_client=None, tools={}, input_text="bad")
    with pytest.raises(ValueError, match="tool_call must include 'tool'"):
        await missing_tool.execute()


@pytest.mark.asyncio
async def test_react_pattern_non_llm_paths_cover_usage_tool_call_pending_and_echo():
    pattern = ReActPattern({"tool_prefix": "/tool", "echo_prefix": "Echo", "max_steps": 2})
    await _setup_pattern(pattern, llm_client=None, tools={"search": _EchoTool()}, input_text="/tool")

    usage_message = await pattern.react()
    assert usage_message == {"type": "final", "content": "Usage: /tool <tool_id> <query>"}

    pattern.context.input_text = "/tool search runtime"
    tool_call = await pattern.react()
    assert tool_call == {"type": "tool_call", "tool": "search", "params": {"query": "runtime"}}
    assert pattern.context.scratch[pattern._PENDING_TOOL_KEY] == "search"

    pattern.context.tool_results.append({"tool_id": "search", "result": {"items": [1]}})
    pending_result = await pattern.react()
    assert pending_result == {"type": "final", "content": "Tool[search] => {'items': [1]}"}

    pattern.context.input_text = "hello"
    pattern.context.memory_view["history"] = [{"input": "first", "output": "second"}, "raw-history"]
    echo_result = await pattern.react()
    assert "Echo: hello" in echo_result["content"]
    assert "User: first" in echo_result["content"]
    assert "raw-history" not in echo_result["content"]


@pytest.mark.asyncio
async def test_react_pattern_llm_execute_and_error_paths():
    llm = _SequenceLLM(['prefix {"type":"tool_call","tool":"search","params":{"query":"runtime"}} suffix'])
    pattern = ReActPattern({"max_steps": 2, "step_timeout_ms": 100})
    await _setup_pattern(pattern, llm_client=llm, tools={"search": _EchoTool()}, input_text="use llm")

    action = await pattern.react()
    assert action == {"type": "tool_call", "tool": "search", "params": {"query": "runtime"}}
    assert pattern.context.scratch[pattern._PENDING_TOOL_KEY] == "search"

    pattern.context.tool_results.append({"tool_id": "search", "result": {"items": ["x"]}})
    result = await pattern.execute()
    assert result == "Tool[search] => {'items': ['x']}"
    assert pattern.context.state["_runtime_last_output"] == result

    class _BadReact(ReActPattern):
        async def react(self):
            return {"type": "tool_call", "tool": "", "params": {}}

    bad = _BadReact({"max_steps": 1})
    await _setup_pattern(bad, llm_client=None, tools={}, input_text="bad")
    with pytest.raises(ValueError, match="tool_call action must include"):
        await bad.execute()

    class _SlowReact(ReActPattern):
        async def react(self):
            import asyncio

            await asyncio.sleep(0.02)
            return {"type": "continue"}

    slow = _SlowReact({"max_steps": 1, "step_timeout_ms": 1})
    await _setup_pattern(slow, llm_client=None, tools={}, input_text="slow")
    with pytest.raises(TimeoutError, match="timed out"):
        await slow.execute()


def test_react_pattern_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        plugin = ReActPattern({"tool_prefix": "/x", "totally_unknown": 1})

    assert plugin._tool_prefix() == "/x"
    assert any(
        "unknown config keys" in r.message and "ReActPattern" in r.message and "totally_unknown" in r.message
        for r in caplog.records
    )


def test_plan_execute_pattern_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        plugin = PlanExecutePattern({"max_steps": 4, "totally_unknown": 1})

    assert plugin._max_steps() == 4
    assert any(
        "unknown config keys" in r.message and "PlanExecutePattern" in r.message and "totally_unknown" in r.message
        for r in caplog.records
    )


def test_reflexion_pattern_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        plugin = ReflexionPattern({"max_retries": 5, "totally_unknown": 1})

    assert plugin._max_retries == 5
    assert any(
        "unknown config keys" in r.message and "ReflexionPattern" in r.message and "totally_unknown" in r.message
        for r in caplog.records
    )
