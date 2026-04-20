from __future__ import annotations

import sys

import pytest

from openagents.plugins.builtin.tool.shell_exec import ShellExecTool


@pytest.mark.asyncio
async def test_runs_command_and_captures_output():
    tool = ShellExecTool(config={})
    result = await tool.invoke(
        {"command": [sys.executable, "-c", "print('hello')"]},
        context=None,
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello"
    assert result["timed_out"] is False
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_times_out():
    tool = ShellExecTool(config={"default_timeout_ms": 200})
    result = await tool.invoke(
        {"command": [sys.executable, "-c", "import time; time.sleep(2)"]},
        context=None,
    )
    assert result["timed_out"] is True
    assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_allowlist_rejects_unlisted_command():
    tool = ShellExecTool(config={"command_allowlist": ["node"]})
    with pytest.raises(ValueError, match="not in allowlist|must be a bare name"):
        await tool.invoke({"command": [sys.executable, "-c", "pass"]}, context=None)


@pytest.mark.asyncio
async def test_string_command_split():
    tool = ShellExecTool(config={})
    result = await tool.invoke(
        {"command": f"{sys.executable} -c \"print('ok')\""},
        context=None,
    )
    assert result["exit_code"] == 0
    assert "ok" in result["stdout"]


@pytest.mark.asyncio
async def test_truncates_large_output():
    tool = ShellExecTool(config={"capture_bytes": 10})
    result = await tool.invoke(
        {"command": [sys.executable, "-c", "print('x' * 1000)"]},
        context=None,
    )
    assert result["truncated"] is True
    assert len(result["stdout"]) <= 10


@pytest.mark.asyncio
async def test_env_passthrough_and_merge(monkeypatch):
    monkeypatch.setenv("FOO_PASS", "value_from_parent")
    monkeypatch.setenv("BAR_BLOCKED", "should_not_leak")
    tool = ShellExecTool(config={"env_passthrough": ["FOO_PASS"]})
    result = await tool.invoke(
        {
            "command": [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('FOO_PASS','')); print(os.environ.get('BAR_BLOCKED',''))",
            ],
            "env": {"EXTRA": "via_invoke"},
        },
        context=None,
    )
    assert "value_from_parent" in result["stdout"]
    assert "should_not_leak" not in result["stdout"]
