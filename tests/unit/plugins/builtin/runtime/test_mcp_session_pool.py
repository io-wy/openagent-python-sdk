"""Tests for the session-scoped MCP pool + preflight dedup (Phase 2).

Covers the new ``_McpSessionCoordinator`` and ``_SessionMcpPool`` types
plus the shared-pool branch in ``McpTool._PooledStrategy.call``. No real
MCP subprocess — the same ``_FakeStdioCM`` / ``_FakeSession`` fakes that
``tests/unit/plugins/builtin/tool/test_mcp_tool.py`` relies on are imported here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from tests.unit.plugins.builtin.tool.test_mcp_tool import (  # noqa: E402  re-use sibling fakes
    _FakeSession,
    _patch_mcp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ScratchCtx:
    """Minimal context stub with a ``scratch`` dict so the shared-pool lookup works."""

    def __init__(self, scratch: dict[str, Any]):
        self.scratch = scratch


def _result(payload: str = "ok"):
    return type("R", (), {"content": [type("C", (), {"text": payload})()], "isError": False})()


# ---------------------------------------------------------------------------
# Shared pool: two tools, one subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_tools_same_identifier_share_one_subprocess():
    """Two McpTool instances with identical server config share one MCP session."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("sess-A")
        ctx = _ScratchCtx({"__mcp_session_pool__": pool})

        t1 = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        t2 = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        # Same server.identifier() (both "echo") → same shared conn.
        await t1.invoke({"tool": "ping"}, context=ctx)
        await t2.invoke({"tool": "ping"}, context=ctx)
        await t1.invoke({"tool": "ping"}, context=ctx)

        await pool.close()

    assert log.count("stdio:enter") == 1
    assert log.count("session:initialize") == 1
    assert log.count("session:call_tool(ping)") == 3
    assert log.count("stdio:exit(None)") == 1


@pytest.mark.asyncio
async def test_two_tools_different_identifier_get_separate_conns():
    """Different server.identifier() values open separate subprocesses."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("sess-B")
        ctx = _ScratchCtx({"__mcp_session_pool__": pool})

        t1 = McpTool(config={"server": {"command": "cmd_A"}, "connection_mode": "pooled"})
        t2 = McpTool(config={"server": {"command": "cmd_B"}, "connection_mode": "pooled"})
        await t1.invoke({"tool": "ping"}, context=ctx)
        await t2.invoke({"tool": "ping"}, context=ctx)

        await pool.close()

    # Two distinct server identifiers → two subprocess spawns.
    assert log.count("stdio:enter") == 2
    assert log.count("stdio:exit(None)") == 2


@pytest.mark.asyncio
async def test_shared_pool_survives_across_runs_on_same_session():
    """Calling invoke() twice in different 'runs' with the same pool reuses one conn."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    coord = _McpSessionCoordinator()

    with _patch_mcp(log, session_factory=session_factory):
        # First "run" — get the pool and invoke once.
        pool_run_1 = await coord.get_or_create("sess-X")
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        await tool.invoke({"tool": "ping"}, context=_ScratchCtx({"__mcp_session_pool__": pool_run_1}))

        # Second "run" — same session_id → same pool instance.
        pool_run_2 = await coord.get_or_create("sess-X")
        assert pool_run_1 is pool_run_2
        await tool.invoke({"tool": "ping"}, context=_ScratchCtx({"__mcp_session_pool__": pool_run_2}))

        await coord.close_all()

    # One subprocess across both "runs".
    assert log.count("stdio:enter") == 1
    assert log.count("session:call_tool(ping)") == 2
    assert log.count("stdio:exit(None)") == 1


@pytest.mark.asyncio
async def test_shared_conn_marked_stale_on_failure_rebuilt_next_call():
    """A failing shared call marks the entry stale; next call opens a new conn."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    calls = {"n": 0}

    def session_factory(reader, writer, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeSession(reader, writer, log, raise_on_call=RuntimeError("dead"))
        return _FakeSession(reader, writer, log, call_result=_result("recovered"))

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("sess-recover")
        ctx = _ScratchCtx({"__mcp_session_pool__": pool})

        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        with pytest.raises(RuntimeError, match="dead"):
            await tool.invoke({"tool": "ping"}, context=ctx)

        # Entry is marked stale; next invoke swaps in a fresh conn.
        out = await tool.invoke({"tool": "ping"}, context=ctx)
        assert out["content"] == ["recovered"]

        await pool.close()

    assert log.count("stdio:enter") == 2


@pytest.mark.asyncio
async def test_tools_cache_populated_from_shared_entry():
    """After sharing a conn, `tool._last_available_tools` mirrors the pool's tools_cache."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    fake_tool_list = [type("T", (), {"name": "ping", "description": "", "inputSchema": {}})()]

    def session_factory(reader, writer, **_kw):
        return _FakeSession(
            reader,
            writer,
            log,
            tools=fake_tool_list,
            call_result=_result(),
        )

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("sess-tc")
        ctx = _ScratchCtx({"__mcp_session_pool__": pool})
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        await tool.invoke({"tool": "ping"}, context=ctx)
        assert tool._last_available_tools is not None
        assert tool._last_available_tools[0]["name"] == "ping"
        await pool.close()


# ---------------------------------------------------------------------------
# Warmup_eager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warmup_eager_opens_conn_before_any_invoke():
    """`prelaunch=eager` tools have their shared conn open before invoke()."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "connection_mode": "pooled",
                "prelaunch": "eager",
            }
        )
        coord = _McpSessionCoordinator()
        pool = await coord.get_or_create("sess-warm")
        await coord.warmup_eager(pool, [tool])

        # The conn is already open — invoke should not open another one.
        assert log.count("stdio:enter") == 1
        assert log.count("session:initialize") == 1

        await tool.invoke(
            {"tool": "ping"},
            context=_ScratchCtx({"__mcp_session_pool__": pool}),
        )
        assert log.count("stdio:enter") == 1  # still one, reused
        await coord.close_all()


@pytest.mark.asyncio
async def test_warmup_eager_skips_non_eager_tools():
    """Tools with prelaunch=off (or per_call) are not warmed up."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        lazy_pooled = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        per_call = McpTool(config={"server": {"command": "echo"}, "connection_mode": "per_call"})
        coord = _McpSessionCoordinator()
        pool = await coord.get_or_create("sess-off")
        await coord.warmup_eager(pool, [lazy_pooled, per_call])

        assert log.count("stdio:enter") == 0  # neither was pre-launched


# ---------------------------------------------------------------------------
# Preflight dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_dedup_success_caches_across_runs():
    """A passing preflight is called once per session — second run is a cache hit."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )

    class _FakeTool:
        def __init__(self):
            self.calls = 0

        async def preflight(self, ctx):
            self.calls += 1

    coord = _McpSessionCoordinator()
    pool = await coord.get_or_create("sess-pf")
    tool = _FakeTool()

    hit1, exc1 = await coord.preflight_with_dedup(pool, tool, "t1")
    hit2, exc2 = await coord.preflight_with_dedup(pool, tool, "t1")

    assert (hit1, exc1) == (False, None)
    assert (hit2, exc2) == (True, None)
    assert tool.calls == 1  # second call skipped


@pytest.mark.asyncio
async def test_preflight_dedup_failure_is_not_cached():
    """Failed preflight retries every time — transient misconfig must be fixable."""
    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )

    class _FailingTool:
        def __init__(self):
            self.calls = 0

        async def preflight(self, ctx):
            self.calls += 1
            raise PermanentToolError("nope", tool_name="t1")

    coord = _McpSessionCoordinator()
    pool = await coord.get_or_create("sess-fail")
    tool = _FailingTool()

    hit1, exc1 = await coord.preflight_with_dedup(pool, tool, "t1")
    hit2, exc2 = await coord.preflight_with_dedup(pool, tool, "t1")

    assert hit1 is False and isinstance(exc1, PermanentToolError)
    assert hit2 is False and isinstance(exc2, PermanentToolError)
    assert tool.calls == 2  # retry is allowed


@pytest.mark.asyncio
async def test_preflight_dedup_tool_without_hook_returns_false_and_none():
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )

    class _NoPreflightTool:
        pass

    coord = _McpSessionCoordinator()
    pool = await coord.get_or_create("sess-no-hook")
    hit, exc = await coord.preflight_with_dedup(pool, _NoPreflightTool(), "t1")
    assert (hit, exc) == (False, None)


# ---------------------------------------------------------------------------
# Shared-pool lookup edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pooled_strategy_falls_back_when_no_scratch_or_no_pool():
    """No context / scratch without the key / invalid pool → per-instance path."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "connection_mode": "pooled",
                "dedup_inflight": False,
            }
        )

        # 1. context=None → own pool path
        await tool.invoke({"tool": "ping"}, context=None)
        # 2. scratch without the pool key → own pool path
        ctx_no_pool = _ScratchCtx({})
        await tool.invoke({"tool": "ping"}, context=ctx_no_pool)
        # 3. scratch with non-pool object → ignored, own pool path
        ctx_bogus = _ScratchCtx({"__mcp_session_pool__": object()})
        await tool.invoke({"tool": "ping"}, context=ctx_bogus)

        await tool.close()

    # All three calls used the same own-instance pool — one subprocess total.
    assert log.count("stdio:enter") == 1
    assert log.count("session:call_tool(ping)") == 3


# ---------------------------------------------------------------------------
# Coordinator lifecycle sanity (pre-Phase-3)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 3: lifecycle — release_session, close drain, LRU, idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_release_session_closes_just_that_pool():
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    coord = _McpSessionCoordinator()
    with _patch_mcp(log, session_factory=session_factory):
        for sid in ("keep", "drop"):
            pool = await coord.get_or_create(sid)
            tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
            await tool.invoke(
                {"tool": "ping"},
                context=_ScratchCtx({"__mcp_session_pool__": pool}),
            )
        assert log.count("stdio:enter") == 2

        await coord.release_session("drop")
        assert log.count("stdio:exit(None)") == 1
        assert coord.list_session_ids() == ["keep"]

        # Double release is a no-op, not an error.
        await coord.release_session("drop")
        assert coord.list_session_ids() == ["keep"]

        await coord.close_all()

    assert log.count("stdio:exit(None)") == 2


@pytest.mark.asyncio
async def test_lru_eviction_closes_oldest_when_at_cap():
    """With max_pooled_sessions=2, a third session evicts the least-recently-used."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    coord = _McpSessionCoordinator(max_pooled_sessions=2)

    async def _run_once(sid: str) -> None:
        pool = await coord.get_or_create(sid)
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        await tool.invoke(
            {"tool": "ping"},
            context=_ScratchCtx({"__mcp_session_pool__": pool}),
        )

    with _patch_mcp(log, session_factory=session_factory):
        await _run_once("s1")
        await _run_once("s2")
        assert set(coord.list_session_ids()) == {"s1", "s2"}
        assert log.count("stdio:enter") == 2
        assert log.count("stdio:exit(None)") == 0

        # Force 's1' to be older. `time.monotonic()` increments implicitly
        # because `_run_once('s2')` touched s2 after s1.
        await _run_once("s3")
        assert set(coord.list_session_ids()) == {"s2", "s3"}  # s1 evicted
        assert log.count("stdio:exit(None)") == 1

        await coord.close_all()

    assert log.count("stdio:exit(None)") == 3


@pytest.mark.asyncio
async def test_idle_eviction_drops_stale_pools_on_get_or_create(monkeypatch):
    from openagents.plugins.builtin.runtime import _mcp_coordinator as coord_mod
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    fake_clock = {"now": 1000.0}

    def _mock_monotonic():
        return fake_clock["now"]

    monkeypatch.setattr(coord_mod.time, "monotonic", _mock_monotonic)

    coord = _McpSessionCoordinator(max_idle_seconds=10.0)
    with _patch_mcp(log, session_factory=session_factory):
        pool_a = await coord.get_or_create("idle_A")
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        await tool.invoke(
            {"tool": "ping"},
            context=_ScratchCtx({"__mcp_session_pool__": pool_a}),
        )

        # Move clock forward past max_idle_seconds.
        fake_clock["now"] += 30.0
        # Any coord.get_or_create call triggers the idle purge.
        await coord.get_or_create("idle_B")
        assert "idle_A" not in coord.list_session_ids()
        assert "idle_B" in coord.list_session_ids()
        assert log.count("stdio:exit(None)") == 1  # pool_a's conn drained

        await coord.close_all()


@pytest.mark.asyncio
async def test_default_runtime_close_drains_mcp_pools():
    """Runtime.close (top-level) -> DefaultRuntime.close -> coordinator.close_all."""
    import openagents.llm.registry as llm_registry
    from openagents.config.loader import load_config_dict
    from openagents.llm.providers.mock import MockLLMClient
    from openagents.runtime.runtime import Runtime

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    payload = {
        "agents": [
            {
                "id": "a1",
                "name": "a1",
                "memory": {"type": "buffer"},
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.FinalPattern",
                },
                "llm": {"provider": "mock"},
                "tools": [
                    {
                        "id": "mcp_demo",
                        "type": "mcp",
                        "config": {
                            "server": {"command": "echo"},
                            "connection_mode": "pooled",
                            "prelaunch": "eager",
                        },
                    }
                ],
            }
        ],
    }
    config = load_config_dict(payload)

    with (
        _patch_mcp(log, session_factory=session_factory),
        patch.object(llm_registry, "create_llm_client", lambda llm: MockLLMClient()),
    ):
        # Ensure `shutil.which` approves the command so preflight passes.
        import shutil as _shutil

        real_which = _shutil.which
        _shutil.which = lambda cmd: "/fake/echo" if cmd == "echo" else real_which(cmd)
        try:
            runtime = Runtime(config)
            from openagents.interfaces.runtime import RunRequest

            await runtime.run_detailed(request=RunRequest(agent_id="a1", session_id="live-sess", input_text="hi"))
            # After the run, the pool must still exist (cross-run survival).
            assert runtime._runtime._mcp_coordinator.list_session_ids() == ["live-sess"]
            assert log.count("stdio:enter") == 1

            await runtime.close()
            assert log.count("stdio:exit(None)") == 1
            assert runtime._runtime._mcp_coordinator.list_session_ids() == []
        finally:
            _shutil.which = real_which


@pytest.mark.asyncio
async def test_release_session_via_runtime_facade():
    """Runtime.release_session drops just that session's MCP pool."""
    import openagents.llm.registry as llm_registry
    from openagents.config.loader import load_config_dict
    from openagents.llm.providers.mock import MockLLMClient
    from openagents.runtime.runtime import Runtime

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    payload = {
        "agents": [
            {
                "id": "a1",
                "name": "a1",
                "memory": {"type": "buffer"},
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.FinalPattern",
                },
                "llm": {"provider": "mock"},
                "tools": [
                    {
                        "id": "mcp_demo",
                        "type": "mcp",
                        "config": {
                            "server": {"command": "echo"},
                            "connection_mode": "pooled",
                            "prelaunch": "eager",
                        },
                    }
                ],
            }
        ],
    }
    config = load_config_dict(payload)

    with (
        _patch_mcp(log, session_factory=session_factory),
        patch.object(llm_registry, "create_llm_client", lambda llm: MockLLMClient()),
    ):
        import shutil as _shutil

        real_which = _shutil.which
        _shutil.which = lambda cmd: "/fake/echo" if cmd == "echo" else real_which(cmd)
        try:
            runtime = Runtime(config)
            from openagents.interfaces.runtime import RunRequest

            for sid in ("sess1", "sess2"):
                await runtime.run_detailed(request=RunRequest(agent_id="a1", session_id=sid, input_text="hi"))
            assert set(runtime._runtime._mcp_coordinator.list_session_ids()) == {
                "sess1",
                "sess2",
            }

            await runtime.release_session("sess1")
            assert runtime._runtime._mcp_coordinator.list_session_ids() == ["sess2"]
            assert log.count("stdio:exit(None)") == 1

            await runtime.close()
        finally:
            _shutil.which = real_which


@pytest.mark.asyncio
async def test_reload_invalidates_mcp_pools_when_agents_change(tmp_path, monkeypatch):
    """Config reload that changes agent config drains all MCP pools."""
    import json

    import openagents.llm.registry as llm_registry
    from openagents.llm.providers.mock import MockLLMClient
    from openagents.runtime.runtime import Runtime

    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    def _write_config(path, version: str, agent_name: str = "a1"):
        payload = {
            "version": version,
            "agents": [
                {
                    "id": "a1",
                    "name": agent_name,
                    "memory": {"type": "buffer"},
                    "pattern": {
                        "impl": "tests.fixtures.runtime_plugins.FinalPattern",
                    },
                    "llm": {"provider": "mock"},
                    "tools": [
                        {
                            "id": "mcp_demo",
                            "type": "mcp",
                            "config": {
                                "server": {"command": "echo"},
                                "connection_mode": "pooled",
                                "prelaunch": "eager",
                            },
                        }
                    ],
                }
            ],
        }
        path.write_text(json.dumps(payload))

    cfg_path = tmp_path / "app.json"
    _write_config(cfg_path, "1.0")

    import shutil as _shutil

    real_which = _shutil.which
    monkeypatch.setattr(
        _shutil,
        "which",
        lambda cmd: "/fake/echo" if cmd == "echo" else real_which(cmd),
    )

    with _patch_mcp(log, session_factory=session_factory):
        runtime = Runtime.from_config(cfg_path)
        from openagents.interfaces.runtime import RunRequest

        await runtime.run_detailed(request=RunRequest(agent_id="a1", session_id="rel-sess", input_text="hi"))
        assert runtime._runtime._mcp_coordinator.list_session_ids() == ["rel-sess"]
        assert log.count("stdio:enter") == 1

        # Change the agent's name so the AgentDefinition `!=` fires and reload
        # flags `a1` in changed_agent_ids.
        _write_config(cfg_path, "1.1", agent_name="a1-renamed")
        await runtime.reload()

        # All MCP pools drained during reload.
        assert runtime._runtime._mcp_coordinator.list_session_ids() == []
        assert log.count("stdio:exit(None)") == 1

        await runtime.close()


@pytest.mark.asyncio
async def test_session_pool_close_is_idempotent():
    """Calling close() on an already-closed pool is a no-op."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("idempotent")
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        await tool.invoke(
            {"tool": "ping"},
            context=_ScratchCtx({"__mcp_session_pool__": pool}),
        )
        await pool.close()
        assert pool.is_closed()
        await pool.close()  # idempotent
    assert log.count("stdio:exit(None)") == 1


@pytest.mark.asyncio
async def test_session_pool_list_tools_failure_is_tolerated():
    """If `list_tools()` fails during open, tools_cache stays None and invoke still works."""
    from openagents.plugins.builtin.runtime._mcp_coordinator import _SessionMcpPool
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    class _NoListTools(_FakeSession):
        async def list_tools(self):
            raise RuntimeError("list_tools not supported by this server")

    def session_factory(reader, writer, **_kw):
        return _NoListTools(reader, writer, log, call_result=_result())

    with _patch_mcp(log, session_factory=session_factory):
        pool = _SessionMcpPool("nolist")
        tool = McpTool(config={"server": {"command": "echo"}, "connection_mode": "pooled"})
        out = await tool.invoke(
            {"tool": "ping"},
            context=_ScratchCtx({"__mcp_session_pool__": pool}),
        )
        assert out == {"content": ["ok"], "isError": False}
        entry = pool._conns["echo"]
        assert entry.tools_cache is None
        await pool.close()


@pytest.mark.asyncio
async def test_coordinator_close_all_drains_every_pool():
    from openagents.plugins.builtin.runtime._mcp_coordinator import (
        _McpSessionCoordinator,
    )
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=_result())

    coord = _McpSessionCoordinator()
    with _patch_mcp(log, session_factory=session_factory):
        for sid in ("s1", "s2"):
            pool = await coord.get_or_create(sid)
            tool = McpTool(
                config={
                    "server": {"command": "echo"},
                    "connection_mode": "pooled",
                }
            )
            await tool.invoke(
                {"tool": "ping"},
                context=_ScratchCtx({"__mcp_session_pool__": pool}),
            )
        assert log.count("stdio:enter") == 2
        await coord.close_all()

    assert log.count("stdio:exit(None)") == 2
    # After close_all, coordinator is empty.
    assert coord.list_session_ids() == []
