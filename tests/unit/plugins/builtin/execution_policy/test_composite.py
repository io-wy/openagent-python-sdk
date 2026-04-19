from __future__ import annotations

import pytest

from openagents.interfaces.tool import (
    PolicyDecision,
    ToolExecutionRequest,
    ToolExecutionSpec,
)
from openagents.plugins.builtin.execution_policy.composite import CompositePolicy


class _Allow:
    def __init__(self, tag: str = "allow"):
        self._tag = tag

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        return PolicyDecision(allowed=True, reason="allow", metadata={"who": self._tag})


class _Deny:
    def __init__(self, tag: str = "deny"):
        self._tag = tag

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason=f"no:{self._tag}", metadata={"who": self._tag})


class _Raise:
    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        raise RuntimeError("boom")


def _req() -> ToolExecutionRequest:
    return ToolExecutionRequest(tool_id="x", tool=object(), execution_spec=ToolExecutionSpec())


@pytest.mark.asyncio
async def test_all_mode_first_deny_wins():
    cp = CompositePolicy(children=[_Allow(), _Deny(tag="d1"), _Deny(tag="d2")], mode="all")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is False
    assert "d1" in decision.reason
    assert decision.metadata["decided_by"] == 1


@pytest.mark.asyncio
async def test_all_allow_passes():
    cp = CompositePolicy(children=[_Allow(), _Allow()], mode="all")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is True
    assert decision.metadata["policy"] == "composite"
    assert len(decision.metadata["children"]) == 2


@pytest.mark.asyncio
async def test_any_mode_first_allow_wins():
    cp = CompositePolicy(children=[_Deny(tag="d"), _Allow()], mode="any")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is True
    assert decision.metadata["decided_by"] == 1


@pytest.mark.asyncio
async def test_empty_children_allows():
    cp = CompositePolicy(children=[], mode="all")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is True
    assert decision.metadata["children"] == []
    assert decision.metadata["decided_by"] == "default"


@pytest.mark.asyncio
async def test_child_exception_wrapped_as_deny():
    cp = CompositePolicy(children=[_Raise()], mode="all")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is False
    assert "raised" in decision.reason
    assert decision.metadata["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_any_mode_all_deny_returns_last_reason():
    cp = CompositePolicy(children=[_Deny(tag="a"), _Deny(tag="b")], mode="any")
    decision = await cp.evaluate_policy(_req())
    assert decision.allowed is False
    assert "b" in decision.reason
    assert decision.metadata["decided_by"] == "none_allowed"


@pytest.mark.asyncio
async def test_composite_with_real_helpers():
    """Integration smoke test: combine FilesystemExecutionPolicy + NetworkAllowlistExecutionPolicy."""
    from openagents.plugins.builtin.execution_policy.filesystem import FilesystemExecutionPolicy
    from openagents.plugins.builtin.execution_policy.network import NetworkAllowlistExecutionPolicy

    fs = FilesystemExecutionPolicy({"deny_tools": ["delete_file"]})
    net = NetworkAllowlistExecutionPolicy({"allow_hosts": ["api.example.com"]})
    cp = CompositePolicy(children=[fs, net], mode="all")

    # A tool neither filesystem nor network cares about should pass both.
    request = ToolExecutionRequest(
        tool_id="some_tool",
        tool=object(),
        params={},
        execution_spec=ToolExecutionSpec(),
    )
    decision = await cp.evaluate_policy(request)
    assert decision.allowed is True

    # A denied tool should be blocked by the filesystem child.
    denied_request = ToolExecutionRequest(
        tool_id="delete_file",
        tool=object(),
        params={},
        execution_spec=ToolExecutionSpec(),
    )
    denied = await cp.evaluate_policy(denied_request)
    assert denied.allowed is False
    assert denied.metadata["decided_by"] == 0
