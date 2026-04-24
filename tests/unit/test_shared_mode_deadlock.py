"""Red test for the 'shared' isolation deadlock (G6 in multi-agent gap analysis).

Before section 2 of `fix-multi-agent-p0-gaps` lands, `InMemorySessionManager.session()`
uses a non-reentrant asyncio.Lock. Nesting `async with session("s")` inside itself on
the same asyncio task hangs forever, which is exactly what happens when an
agent_router.delegate(..., session_isolation='shared') recurses into Runtime.run_detailed.

`asyncio.wait_for` trips the explicit timeout so the deadlock surfaces as a
`TimeoutError` instead of hanging pytest.
"""

from __future__ import annotations

import asyncio

import pytest

from openagents.plugins.builtin.session.in_memory import InMemorySessionManager


@pytest.mark.asyncio
async def test_nested_session_same_id_does_not_deadlock():
    """Regression for agent_router.delegate(..., session_isolation='shared')."""
    mgr = InMemorySessionManager()

    async def scenario():
        async with mgr.session("s1") as _parent_state:
            async with mgr.session("s1") as _child_state:
                pass

    # A timeout here means the nested acquire never returned — the deadlock.
    await asyncio.wait_for(scenario(), timeout=2.0)
