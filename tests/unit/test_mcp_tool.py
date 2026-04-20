"""Tests for the MCP ToolPlugin adapter.

The adapter wraps ``mcp.client.stdio.stdio_client`` + ``mcp.ClientSession``,
both of which install ``anyio`` task-group cancel scopes on the entering
task. Those scopes MUST be unwound inside the same ``invoke()`` call that
opened them, otherwise a later subprocess crash cancels whatever the
caller is doing next (the historical tavily_fallback-cancelled bug).

These tests pin the invariants:
- Each ``invoke()`` call opens AND closes a fresh session.
- ``session.initialize()`` is called before ``list_tools``/``call_tool``.
- Closing happens even when ``call_tool`` raises, in the same task.
- ``self._connection`` is not cached across calls, so a failed call cannot
  leak state into a subsequent one.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup  # noqa: F401


# ---------------------------------------------------------------------------
# Fakes that mimic the MCP SDK's async-context-manager protocol without
# spawning a subprocess or installing any real anyio task groups. They let
# us observe enter/exit ordering from the same event-loop task.
# ---------------------------------------------------------------------------


class _FakeStdioCM:
    """Stand-in for ``stdio_client(params)``'s async context manager."""

    def __init__(self, log: list[str]):
        self._log = log

    async def __aenter__(self):
        self._log.append("stdio:enter")
        return ("fake-reader", "fake-writer")

    async def __aexit__(self, exc_type, exc, tb):
        self._log.append(f"stdio:exit({exc_type.__name__ if exc_type else 'None'})")
        return None


class _FakeSession:
    """Stand-in for ``mcp.ClientSession``."""

    def __init__(
        self,
        reader,
        writer,
        log: list[str],
        *,
        tools: list[Any] | None = None,
        call_result: Any | None = None,
        raise_on_call: BaseException | None = None,
    ):
        self._log = log
        self._tools = tools or []
        self._call_result = call_result
        self._raise_on_call = raise_on_call
        self.initialized = False

    async def __aenter__(self):
        self._log.append("session:enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._log.append(f"session:exit({exc_type.__name__ if exc_type else 'None'})")
        return None

    async def initialize(self):
        self._log.append("session:initialize")
        self.initialized = True

        class _Result:
            pass

        return _Result()

    async def list_tools(self):
        if not self.initialized:  # pragma: no cover - safety check
            raise AssertionError("list_tools before initialize()")

        class _Resp:
            def __init__(self, tools):
                self.tools = tools

        return _Resp(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        if not self.initialized:  # pragma: no cover - safety check
            raise AssertionError("call_tool before initialize()")
        self._log.append(f"session:call_tool({name})")
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._call_result


def _patch_mcp(log: list[str], *, session_factory):
    """Patch the imports McpConnection makes inside its connect helpers."""

    def _stdio_client(_params):
        return _FakeStdioCM(log)

    # StdioServerParameters is just a dataclass, so a dummy sentinel works.
    class _DummyParams:
        def __init__(self, **kw):
            self.kw = kw

    fake_mcp = type(
        "fake_mcp",
        (),
        {
            "ClientSession": session_factory,
            "StdioServerParameters": _DummyParams,
        },
    )
    fake_stdio_module = type("fake_stdio", (), {"stdio_client": _stdio_client})

    return patch.dict(
        "sys.modules",
        {"mcp": fake_mcp, "mcp.client.stdio": fake_stdio_module},
    )


@pytest.mark.asyncio
async def test_invoke_opens_and_closes_session_per_call():
    """Each invoke() spawns a fresh session that is cleanly unwound."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type("R", (), {"content": [type("C", (), {"text": "hi"})()], "isError": False})()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo", "args": []}})
        out = await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

    assert out == {"content": ["hi"], "isError": False}
    # Enter/exit order is LIFO and all in one call:
    assert log == [
        "stdio:enter",
        "session:enter",
        "session:initialize",
        "session:call_tool(ping)",
        "session:exit(None)",
        "stdio:exit(None)",
    ]


@pytest.mark.asyncio
async def test_invoke_unwinds_on_call_tool_failure():
    """If call_tool raises, both sessions are still closed on the same task."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(
            reader,
            writer,
            log,
            raise_on_call=RuntimeError("subprocess died"),
        )

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(RuntimeError, match="subprocess died"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

    # Exits must still fire, in reverse order, on the same task:
    assert "session:exit(RuntimeError)" in log
    assert "stdio:exit(RuntimeError)" in log
    assert log.index("session:exit(RuntimeError)") < log.index("stdio:exit(RuntimeError)")


@pytest.mark.asyncio
async def test_failed_invoke_does_not_leak_into_next_call():
    """A failed invoke() leaves NO cached session; the next call is fresh.

    This is the regression guard for the tavily_fallback cancellation bug:
    previously, a failed MCP invoke left an anyio cancel scope on the
    caller's task, which later cancelled the fallback.
    """
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    success_result = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()
    calls = {"n": 0}

    def session_factory(reader, writer, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeSession(reader, writer, log, raise_on_call=RuntimeError("boom"))
        return _FakeSession(reader, writer, log, call_result=success_result)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})

        with pytest.raises(RuntimeError, match="boom"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

        # No cached state survived the failure — and the second call works:
        assert tool._last_available_tools == []
        out = await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

    assert out == {"content": ["ok"], "isError": False}
    # The log shows two complete open/close cycles, no leaks:
    assert log.count("stdio:enter") == 2
    assert log.count("stdio:exit(RuntimeError)") == 1
    assert log.count("stdio:exit(None)") == 1


@pytest.mark.asyncio
async def test_rejects_tool_outside_exposed_list():
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):  # pragma: no cover - unreachable
        return _FakeSession(reader, writer, log)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "tools": ["read_file"],
            }
        )
        with pytest.raises(ValueError, match="not exposed"):
            await tool.invoke({"tool": "write_file"}, context=None)

    # No session was opened because the guard fires before AsyncExitStack.
    assert log == []


@pytest.mark.asyncio
async def test_close_is_a_noop_after_sessionless_lifecycle():
    """``close()`` is kept for backward-compat but owns no resources now."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    tool = McpTool(config={"server": {"command": "echo"}})
    # Should not raise even though we've never invoked.
    await tool.close()
    # Idempotent.
    await tool.close()


@pytest.mark.asyncio
async def test_missing_tool_param_rejected_before_connecting():
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):  # pragma: no cover - unreachable
        return _FakeSession(reader, writer, log)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(ValueError, match="'tool' parameter"):
            await tool.invoke({}, context=None)

    assert log == []


def test_mcp_tool_runs_on_current_event_loop():
    """Sanity: the fakes don't require a special event loop."""
    asyncio.new_event_loop().close()


@pytest.mark.asyncio
async def test_invoke_unwraps_single_exceptiongroup():
    """When AsyncExitStack re-raises an anyio task-group ExceptionGroup
    containing a single sub-exception, invoke() should surface the real
    cause instead of the opaque group repr.
    """
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    class _GroupRaisingStdioCM:
        async def __aenter__(self):
            log.append("stdio:enter")
            return ("fake-reader", "fake-writer")

        async def __aexit__(self, exc_type, exc, tb):
            log.append("stdio:exit")
            # Simulate anyio task group wrapping the actual error.
            raise BaseExceptionGroup(
                "unhandled errors in a TaskGroup",
                [ConnectionError("stdin pipe closed unexpectedly")],
            )

    def _stdio_client(_params):
        return _GroupRaisingStdioCM()

    class _DummyParams:
        def __init__(self, **_kw):
            pass

    def session_factory(reader, writer, **_kw):
        return _FakeSession(
            reader,
            writer,
            log,
            call_result=type(
                "R",
                (),
                {"content": [], "isError": False},
            )(),
        )

    fake_mcp = type(
        "fake_mcp",
        (),
        {
            "ClientSession": session_factory,
            "StdioServerParameters": _DummyParams,
        },
    )
    fake_stdio = type("fake_stdio", (), {"stdio_client": _stdio_client})

    with patch.dict(
        "sys.modules",
        {"mcp": fake_mcp, "mcp.client.stdio": fake_stdio},
    ):
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(ConnectionError, match="stdin pipe closed"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)


# ---------------------------------------------------------------------------
# Pooled connection mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pooled_mode_opens_one_session_across_three_calls():
    """Three sequential invoke() calls in pooled mode open exactly ONE stdio
    context and ONE initialize(); the session is reused."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "connection_mode": "pooled",
                "dedup_inflight": False,
            }
        )
        for _ in range(3):
            out = await tool.invoke({"tool": "ping", "arguments": {}}, context=None)
            assert out == {"content": ["ok"], "isError": False}
        await tool.close()

    # Exactly one full lifecycle: one enter, one initialize, one exit.
    assert log.count("stdio:enter") == 1
    assert log.count("session:initialize") == 1
    assert log.count("stdio:exit(None)") == 1
    # Three call_tool invocations, not three sessions.
    assert log.count("session:call_tool(ping)") == 3


@pytest.mark.asyncio
async def test_pooled_close_is_idempotent():
    """close() drains the pool once; calling it again is a no-op."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "connection_mode": "pooled",
                "dedup_inflight": False,
            }
        )
        await tool.invoke({"tool": "ping", "arguments": {}}, context=None)
        await tool.close()
        await tool.close()  # second close must not raise

    assert log.count("stdio:exit(None)") == 1


@pytest.mark.asyncio
async def test_pooled_recovers_from_dead_session_on_next_call():
    """When a pooled call fails, the pool is marked stale and the next call
    opens a fresh session without cancelling the caller."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    good_result = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()
    calls = {"n": 0}

    def session_factory(reader, writer, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeSession(reader, writer, log, raise_on_call=RuntimeError("died"))
        return _FakeSession(reader, writer, log, call_result=good_result)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "connection_mode": "pooled",
                "dedup_inflight": False,
            }
        )
        with pytest.raises(RuntimeError, match="died"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)
        # Next call must succeed — no leaked cancel scope.
        out = await tool.invoke({"tool": "ping", "arguments": {}}, context=None)
        assert out == {"content": ["ok"], "isError": False}
        await tool.close()

    # Two sessions total: the dead one plus the fresh swap.
    assert log.count("stdio:enter") == 2


# ---------------------------------------------------------------------------
# SSE / HTTP transport
# ---------------------------------------------------------------------------


class _FakeSseCM:
    """Stand-in for the context manager ``sse_client(url, headers=...)``
    returns. Mirrors the stdio fake: logs enter/exit and yields a
    reader/writer pair used only as opaque sentinels by the session."""

    def __init__(self, log: list[str]):
        self._log = log

    async def __aenter__(self):
        self._log.append("sse:enter")
        return ("fake-sse-reader", "fake-sse-writer")

    async def __aexit__(self, exc_type, exc, tb):
        self._log.append(f"sse:exit({exc_type.__name__ if exc_type else 'None'})")
        return None


def _patch_mcp_sse(log: list[str], *, session_factory, sse_client_fn=None):
    def _default_sse(*, url, headers):
        log.append(f"sse_client(url={url})")
        return _FakeSseCM(log)

    sse_fn = sse_client_fn if sse_client_fn is not None else _default_sse

    fake_mcp = type(
        "fake_mcp",
        (),
        {
            "ClientSession": session_factory,
        },
    )
    fake_sse_module = type("fake_sse", (), {"sse_client": sse_fn})

    return patch.dict(
        "sys.modules",
        {"mcp": fake_mcp, "mcp.client.sse": fake_sse_module},
    )


@pytest.mark.asyncio
async def test_url_configured_tool_routes_through_sse_client():
    """An ``McpTool`` with ``server.url`` opens the SSE transport and
    initializes the session in the correct order."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "hi"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp_sse(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"url": "http://example.test/mcp"}})
        out = await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

    assert out == {"content": ["hi"], "isError": False}
    # Enter/exit order matches stdio: transport before session, both exit in
    # reverse order on the same task.
    assert log == [
        "sse_client(url=http://example.test/mcp)",
        "sse:enter",
        "session:enter",
        "session:initialize",
        "session:call_tool(ping)",
        "session:exit(None)",
        "sse:exit(None)",
    ]


@pytest.mark.asyncio
async def test_sse_path_unwinds_on_call_failure():
    """When call_tool fails on the SSE path, both contexts exit on the same task."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        return _FakeSession(
            reader,
            writer,
            log,
            raise_on_call=RuntimeError("sse broke"),
        )

    with _patch_mcp_sse(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"url": "http://example.test/mcp"}})
        with pytest.raises(RuntimeError, match="sse broke"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)

    assert "session:exit(RuntimeError)" in log
    assert "sse:exit(RuntimeError)" in log
    assert log.index("session:exit(RuntimeError)") < log.index("sse:exit(RuntimeError)")


@pytest.mark.asyncio
async def test_missing_sse_client_symbol_raises_actionable_error():
    """If the installed mcp SDK doesn't expose sse_client, the tool raises
    a RuntimeError naming the SDK version and upgrade hint — NOT a raw
    ImportError that hides the root cause."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    fake_mcp_pkg = type(
        "fake_mcp",
        (),
        {"ClientSession": object, "__version__": "0.0.1"},
    )
    # ``mcp.client.sse`` is NOT registered in sys.modules, so the import in
    # ``_connect_http`` will fail with ImportError — which the tool must
    # translate into a RuntimeError.
    with patch.dict("sys.modules", {"mcp": fake_mcp_pkg}):
        # Ensure the SSE submodule isn't cached from a prior test.
        import sys as _sys

        _sys.modules.pop("mcp.client.sse", None)
        tool = McpTool(config={"server": {"url": "http://example.test/mcp"}})
        with pytest.raises(RuntimeError, match="sse_client"):
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_missing_mcp_import_raises_with_install_hint():
    """Preflight fails fast when the mcp package isn't importable."""
    # Put 'mcp' in sys.modules as None so import raises ImportError.
    import sys as _sys

    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    original = _sys.modules.get("mcp")
    _sys.modules["mcp"] = None  # sentinel: "this module is known-unavailable"
    try:
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(PermanentToolError, match="uv sync --extra mcp"):
            await tool.preflight(None)
    finally:
        if original is not None:
            _sys.modules["mcp"] = original
        else:
            _sys.modules.pop("mcp", None)


@pytest.mark.asyncio
async def test_preflight_missing_stdio_command_fails_without_forking():
    """A stdio server whose command isn't on PATH is rejected up-front."""
    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    # Provide a fake mcp so the import check passes; then the command check
    # should fire before any subprocess would be forked.
    fake_mcp = type("fake_mcp", (), {"ClientSession": object})
    with patch.dict("sys.modules", {"mcp": fake_mcp}):
        tool = McpTool(
            config={"server": {"command": "definitely_not_a_real_cmd_xyz_7f3"}},
        )
        with pytest.raises(PermanentToolError, match="not found on PATH"):
            await tool.preflight(None)


@pytest.mark.asyncio
async def test_preflight_rejects_bad_url():
    """URL without scheme/netloc is rejected as invalid config."""
    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    fake_mcp = type("fake_mcp", (), {"ClientSession": object})
    with patch.dict("sys.modules", {"mcp": fake_mcp}):
        tool = McpTool(config={"server": {"url": "not-a-url"}})
        with pytest.raises(PermanentToolError, match="not a valid URL"):
            await tool.preflight(None)


@pytest.mark.asyncio
async def test_preflight_probe_surfaces_server_startup_failure():
    """When probe_on_preflight=True, a server whose first list_tools() call
    fails surfaces as a PermanentToolError *before* the agent loop runs."""
    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    def session_factory(reader, writer, **_kw):
        # This fake's list_tools() raises instead of returning a tools list.
        session = _FakeSession(reader, writer, log)

        async def _boom():
            raise RuntimeError("server crashed on startup")

        session.list_tools = _boom  # type: ignore[assignment]
        return session

    # Point `shutil.which` at an existing binary so the command check passes.
    import shutil as _shutil

    real_which = _shutil.which
    try:
        _shutil.which = lambda cmd: "/fake/path/echo" if cmd == "echo" else real_which(cmd)
        with _patch_mcp(log, session_factory=session_factory):
            tool = McpTool(
                config={
                    "server": {"command": "echo"},
                    "probe_on_preflight": True,
                }
            )
            with pytest.raises(PermanentToolError, match="preflight probe failed"):
                await tool.preflight(None)
    finally:
        _shutil.which = real_which


# ---------------------------------------------------------------------------
# In-flight dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_coalesces_two_concurrent_identical_calls():
    """Two concurrent invoke() calls with identical (tool, args) open ONE
    session and both awaits return the same result."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "shared"})()], "isError": False},
    )()
    block = asyncio.Event()

    class _BlockingSession(_FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]):
            # Hold inside call_tool so the second call starts while we're
            # still running and can observe the in-flight future.
            self._log.append(f"session:call_tool({name})")
            await block.wait()
            return self._call_result

    def session_factory(reader, writer, **_kw):
        s = _BlockingSession(reader, writer, log, call_result=result_obj)
        return s

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})

        async def _drive():
            return await tool.invoke({"tool": "ping", "arguments": {"k": 1}}, context=None)

        task_a = asyncio.create_task(_drive())
        task_b = asyncio.create_task(_drive())

        # Yield so both tasks enter invoke() and register the in-flight future.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        block.set()

        a, b = await asyncio.gather(task_a, task_b)

    assert a == b == {"content": ["shared"], "isError": False}
    # Only one session was opened for two callers.
    assert log.count("stdio:enter") == 1
    assert log.count("session:call_tool(ping)") == 1


@pytest.mark.asyncio
async def test_dedup_does_not_coalesce_different_arguments():
    """Different arguments open separate sessions."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        results = await asyncio.gather(
            tool.invoke({"tool": "ping", "arguments": {"k": 1}}, context=None),
            tool.invoke({"tool": "ping", "arguments": {"k": 2}}, context=None),
        )

    assert all(r == {"content": ["ok"], "isError": False} for r in results)
    assert log.count("stdio:enter") == 2


@pytest.mark.asyncio
async def test_dedup_disabled_opens_separate_sessions():
    """dedup_inflight=False forces a fresh session per call even for identical args."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(
            config={
                "server": {"command": "echo"},
                "dedup_inflight": False,
            }
        )
        # Serial calls — with dedup on these would still spawn a second
        # session since the first is already complete by the time the
        # second runs. The interesting case is that the two calls never
        # share a future, which we verify by checking stdio:enter count.
        await tool.invoke({"tool": "ping", "arguments": {"k": 1}}, context=None)
        await tool.invoke({"tool": "ping", "arguments": {"k": 1}}, context=None)

    assert log.count("stdio:enter") == 2


@pytest.mark.asyncio
async def test_failed_dedup_call_does_not_poison_next_call():
    """After a coalesced call fails, the in-flight key is cleared so the
    next identical call opens a fresh session and is not re-awaiting the
    failed future."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    success_result = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "ok"})()], "isError": False},
    )()
    calls = {"n": 0}

    def session_factory(reader, writer, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeSession(reader, writer, log, raise_on_call=RuntimeError("boom"))
        return _FakeSession(reader, writer, log, call_result=success_result)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(RuntimeError, match="boom"):
            await tool.invoke({"tool": "ping", "arguments": {"x": 1}}, context=None)

        # Same args — if the failed future were still cached, we'd re-await it
        # and raise again. Instead we expect a fresh session that succeeds.
        out = await tool.invoke({"tool": "ping", "arguments": {"x": 1}}, context=None)
        assert out == {"content": ["ok"], "isError": False}


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class _FakeContext:
    """Minimal RunContext stand-in with an ``event_bus``."""

    def __init__(self, bus):
        self.event_bus = bus


class _RecordingBus:
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, **payload: Any) -> None:
        self.events.append((event, payload))


@pytest.mark.asyncio
async def test_events_never_include_arguments_or_results():
    """Emitted MCP events must not leak tool arguments or tool results — only
    identifiers, status, and timing. This is a privacy invariant."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    secret_arg = "DO-NOT-LOG-ME"
    secret_result = "ALSO-DO-NOT-LOG-ME"
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": secret_result})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    bus = _RecordingBus()
    ctx = _FakeContext(bus)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        await tool.invoke(
            {"tool": "ping", "arguments": {"secret": secret_arg}},
            context=ctx,
        )

    serialized = repr(bus.events)
    assert secret_arg not in serialized
    assert secret_result not in serialized
    # Expected events emitted:
    names = [name for name, _ in bus.events]
    assert "tool.mcp.connect" in names
    assert "tool.mcp.call" in names


@pytest.mark.asyncio
async def test_invoke_without_event_bus_does_not_raise():
    """When no event bus is attached to the context, emission is a no-op."""
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []
    result_obj = type(
        "R",
        (),
        {"content": [type("C", (), {"text": "hi"})()], "isError": False},
    )()

    def session_factory(reader, writer, **_kw):
        return _FakeSession(reader, writer, log, call_result=result_obj)

    with _patch_mcp(log, session_factory=session_factory):
        tool = McpTool(config={"server": {"command": "echo"}})
        # context=None means no bus; must not raise.
        await tool.invoke({"tool": "ping", "arguments": {}}, context=None)


@pytest.mark.asyncio
async def test_preflight_event_emitted_on_error():
    """A failed preflight emits a 'tool.mcp.preflight' event with result=error
    before propagating the PermanentToolError."""
    from openagents.errors.exceptions import PermanentToolError
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    fake_mcp = type("fake_mcp", (), {"ClientSession": object})
    bus = _RecordingBus()
    ctx = _FakeContext(bus)

    with patch.dict("sys.modules", {"mcp": fake_mcp}):
        tool = McpTool(config={"server": {"command": "nope_cmd_1234"}})
        with pytest.raises(PermanentToolError):
            await tool.preflight(ctx)

    preflight_events = [p for n, p in bus.events if n == "tool.mcp.preflight"]
    assert preflight_events
    assert preflight_events[-1]["result"] == "error"


@pytest.mark.asyncio
async def test_invoke_preserves_multichild_exceptiongroup():
    """Groups with multiple sub-exceptions are NOT unwrapped — surface the
    full group so information isn't lost.
    """
    from openagents.plugins.builtin.tool.mcp_tool import McpTool

    log: list[str] = []

    class _MultiGroupStdioCM:
        async def __aenter__(self):
            return ("r", "w")

        async def __aexit__(self, exc_type, exc, tb):
            raise BaseExceptionGroup(
                "two things broke",
                [RuntimeError("a"), RuntimeError("b")],
            )

    def _stdio_client(_params):
        return _MultiGroupStdioCM()

    class _DummyParams:
        def __init__(self, **_kw):
            pass

    def session_factory(reader, writer, **_kw):
        return _FakeSession(
            reader,
            writer,
            log,
            call_result=type(
                "R",
                (),
                {"content": [], "isError": False},
            )(),
        )

    fake_mcp = type(
        "fake_mcp",
        (),
        {
            "ClientSession": session_factory,
            "StdioServerParameters": _DummyParams,
        },
    )
    fake_stdio = type("fake_stdio", (), {"stdio_client": _stdio_client})

    with patch.dict(
        "sys.modules",
        {"mcp": fake_mcp, "mcp.client.stdio": fake_stdio},
    ):
        tool = McpTool(config={"server": {"command": "echo"}})
        with pytest.raises(BaseExceptionGroup) as exc_info:
            await tool.invoke({"tool": "ping", "arguments": {}}, context=None)
        assert len(exc_info.value.exceptions) == 2
