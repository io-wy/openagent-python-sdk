"""Tests for SafeToolExecutor command_allowlist (P1)."""

from __future__ import annotations

import pytest

from openagents.interfaces.tool import ToolExecutionRequest
from openagents.plugins.builtin.tool.shell_exec import ShellExecTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _StubTool:
    tool_id = "stub"
    async def invoke(self, params, context):
        return "ok"


def _request(tool_id: str, params: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_id=tool_id,
        tool=_StubTool(),
        params=params,
    )


@pytest.mark.asyncio
async def test_allowlist_none_allows_all():
    executor = SafeToolExecutor(config={})
    req = _request("shell_exec", {"command": ["rm", "-rf", "/"]})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_allowlist_blocks_unlisted_shell_command():
    executor = SafeToolExecutor(config={"command_allowlist": ["node", "python"]})
    req = _request("shell_exec", {"command": ["rm", "-rf", "/"]})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is False
    assert "not in allowlist" in decision.reason


@pytest.mark.asyncio
async def test_allowlist_allows_listed_shell_command():
    executor = SafeToolExecutor(config={"command_allowlist": ["node", "python"]})
    req = _request("shell_exec", {"command": ["node", "-e", "console.log(1)"]})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_allowlist_allows_string_command():
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    req = _request("shell_exec", {"command": "python -c 'print(1)'"})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_allowlist_rejects_string_command_not_in_list():
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    req = _request("shell_exec", {"command": "rm -rf /"})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_allowlist_rejects_path_like_argv0():
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    req = _request("shell_exec", {"command": ["/usr/bin/python", "-c", "pass"]})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is False
    assert "bare name" in decision.reason


@pytest.mark.asyncio
async def test_allowlist_ignores_non_shell_tools():
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    req = _request("read_file", {"path": "/etc/passwd"})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_allowlist_rejects_empty_command():
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    req = _request("shell_exec", {"command": ""})
    decision = await executor.evaluate_policy(req)
    assert decision.allowed is False
    assert "empty" in decision.reason


@pytest.mark.asyncio
async def test_allowlist_integration_via_execute():
    """Full stack: executor.evaluate_policy denies before tool.invoke runs."""
    executor = SafeToolExecutor(config={"command_allowlist": ["python"]})
    tool = ShellExecTool(config={})
    req = ToolExecutionRequest(
        tool_id="shell_exec",
        tool=tool,
        params={"command": ["node", "-e", "console.log(1)"]},
    )
    result = await executor.execute(req)
    assert result.success is False
    assert "not in allowlist" in result.error
