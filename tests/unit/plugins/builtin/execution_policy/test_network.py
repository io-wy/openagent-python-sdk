from __future__ import annotations

import pytest

from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionSpec
from openagents.plugins.builtin.execution_policy.network import NetworkAllowlistExecutionPolicy


def _req(tool_id: str = "http_request", url: str | None = "https://api.example.com/v1") -> ToolExecutionRequest:
    params = {"url": url} if url is not None else {}
    return ToolExecutionRequest(tool_id=tool_id, tool=object(), params=params, execution_spec=ToolExecutionSpec())


def _make(**config) -> NetworkAllowlistExecutionPolicy:
    cfg = {"allow_hosts": ["api.example.com"], **config}
    return NetworkAllowlistExecutionPolicy(config=cfg)


@pytest.mark.asyncio
async def test_exact_host_allowed():
    decision = await _make().evaluate_policy(_req())
    assert decision.allowed is True
    assert decision.metadata["host"] == "api.example.com"


@pytest.mark.asyncio
async def test_wildcard_host_allowed():
    policy = _make(allow_hosts=["*.example.com"])
    decision = await policy.evaluate_policy(_req(url="https://edge.example.com/x"))
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_unlisted_host_denied():
    decision = await _make().evaluate_policy(_req(url="https://evil.test/x"))
    assert decision.allowed is False
    assert "not in allow_hosts" in decision.reason


@pytest.mark.asyncio
async def test_scheme_denied():
    policy = _make(allow_schemes=["https"])
    decision = await policy.evaluate_policy(_req(url="http://api.example.com/x"))
    assert decision.allowed is False
    assert "scheme" in decision.reason


@pytest.mark.asyncio
async def test_non_applicable_tool_allowed():
    policy = _make(applies_to_tools=["http_request"])
    decision = await policy.evaluate_policy(_req(tool_id="read_file", url=None))
    assert decision.allowed is True
    assert decision.metadata.get("skipped") is True


@pytest.mark.asyncio
async def test_private_network_denied_when_flag_on():
    policy = _make(allow_hosts=["127.0.0.1", "10.0.0.5", "192.168.1.2", "172.20.0.1"], deny_private_networks=True)
    for url in ("http://127.0.0.1/x", "http://10.0.0.5/x", "http://192.168.1.2/x", "http://172.20.0.1/x"):
        decision = await policy.evaluate_policy(_req(url=url))
        assert decision.allowed is False, url


@pytest.mark.asyncio
async def test_public_172_32_not_denied_by_private_check():
    """172.32.x.x is public address space — must not be blanket-denied."""
    policy = _make(allow_hosts=["172.32.0.1"], deny_private_networks=True, allow_schemes=["http"])
    decision = await policy.evaluate_policy(_req(url="http://172.32.0.1/x"))
    assert decision.allowed is True, "172.32.0.1 is public and should pass when on allow_hosts"


@pytest.mark.asyncio
async def test_172_15_not_denied_by_private_check():
    """172.15.x.x sits just below the private range and is public."""
    policy = _make(allow_hosts=["172.15.0.1"], deny_private_networks=True, allow_schemes=["http"])
    decision = await policy.evaluate_policy(_req(url="http://172.15.0.1/x"))
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_private_network_allowed_when_flag_off():
    policy = _make(allow_hosts=["127.0.0.1"], deny_private_networks=False, allow_schemes=["http"])
    decision = await policy.evaluate_policy(_req(url="http://127.0.0.1/x"))
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_unparseable_url_denied():
    decision = await _make().evaluate_policy(_req(url=""))
    assert decision.allowed is False
