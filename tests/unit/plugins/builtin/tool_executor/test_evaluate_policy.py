import pytest

from openagents.interfaces.tool import PolicyDecision, ToolExecutionRequest, ToolExecutorPlugin


class _MinimalTool:
    async def invoke(self, params, context):
        return "ok"


@pytest.mark.asyncio
async def test_default_evaluate_policy_allows_all():
    executor = ToolExecutorPlugin()
    req = ToolExecutionRequest(tool_id="t", tool=_MinimalTool(), params={})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_execute_calls_evaluate_policy_and_denies():
    class DenyExecutor(ToolExecutorPlugin):
        async def evaluate_policy(self, request):
            return PolicyDecision(allowed=False, reason="nope")

    executor = DenyExecutor()
    req = ToolExecutionRequest(tool_id="t", tool=_MinimalTool(), params={})
    result = await executor.execute(req)
    assert result.success is False
    assert "nope" in result.error


@pytest.mark.asyncio
async def test_execute_calls_evaluate_policy_and_allows():
    executor = ToolExecutorPlugin()
    req = ToolExecutionRequest(tool_id="t", tool=_MinimalTool(), params={})
    result = await executor.execute(req)
    assert result.success is True
    assert result.data == "ok"
