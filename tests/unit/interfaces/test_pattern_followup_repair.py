from unittest.mock import MagicMock

import pytest

from openagents.interfaces.pattern import PatternPlugin


def _make_pattern():
    p = PatternPlugin()
    p.context = MagicMock()
    return p


@pytest.mark.asyncio
async def test_resolve_followup_default_returns_none():
    p = _make_pattern()
    result = await p.resolve_followup(context=p.context)
    assert result is None


@pytest.mark.asyncio
async def test_repair_empty_response_default_returns_none():
    p = _make_pattern()
    result = await p.repair_empty_response(
        context=p.context,
        messages=[],
        assistant_content=[],
        stop_reason=None,
        retries=0,
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_followup_can_be_overridden():
    from openagents.interfaces.followup import FollowupResolution

    class MyPattern(PatternPlugin):
        async def resolve_followup(self, *, context):
            return FollowupResolution(status="resolved", output="42")

    p = MyPattern()
    p.context = MagicMock()
    result = await p.resolve_followup(context=p.context)
    assert result.status == "resolved"
    assert result.output == "42"
