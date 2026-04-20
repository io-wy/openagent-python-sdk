"""MCP (Model Context Protocol) tool plugin.

Connection lifecycle:

- ``per_call`` (default): every ``invoke()`` spawns a fresh stdio
  subprocess (or SSE session), initializes, runs one call, and tears
  down — all inside the same event-loop task. This preserves the
  anyio cancel-scope invariant that a subprocess crash can't cancel
  whatever the caller does next. This is the historical behavior and
  the default; do not break the ordering guarantees in
  ``tests/unit/test_mcp_tool.py``.

- ``pooled``: opens one long-lived ``McpConnection`` on first call,
  reuses it for subsequent calls, serialized through an
  ``asyncio.Lock``. Drained by ``close()``. Gives up the per-call
  cancel-scope bound in exchange for N× fewer subprocesses. Dead
  subprocess detection swaps on the *next* call, not inside the
  failing call, to avoid leaking the dying session's cancel scope
  into the caller.

``preflight()`` runs once per session before the agent loop starts
and verifies the ``mcp`` extra is importable and the server config
is valid, so misconfiguration is caught up-front instead of mid-run.

In-flight dedup coalesces concurrent ``invoke()`` calls that share
the same ``(tool_name, canonical-arguments)`` key in ``per_call``
mode. Pooled mode already serializes through the session lock.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import shutil
import time
import urllib.parse
import weakref
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin

logger = logging.getLogger(__name__)


def _unwrap_single_exception(err: BaseException) -> BaseException:
    """Peel a chain of single-child ExceptionGroups down to the real cause."""
    while isinstance(err, BaseExceptionGroup) and len(err.exceptions) == 1:  # noqa: F821
        err = err.exceptions[0]
    return err


def _canonical_args_hash(arguments: dict[str, Any]) -> str:
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        payload = repr(sorted(arguments.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class McpServerConfig:
    """MCP server connection configuration."""

    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None

    def identifier(self) -> str:
        if self.url:
            return self.url
        return self.command or "<unset>"


class McpConnection:
    """Short-lived MCP session used as an ``async with`` block.

    ``stdio_client`` and ``ClientSession`` both open ``anyio`` task groups
    whose cancel scopes attach to the entering task. They MUST be entered
    and exited in the same task and within the same ``async with`` block;
    otherwise the scope outlives the call, and when the remote side dies
    the leaked scope cancels the caller's next ``await`` (including any
    fallback path). That's exactly the "Connection closed" /
    ``'Attempted to exit cancel scope in a different task...'`` failure
    mode we previously saw when tavily-mcp crashed and the REST fallback
    got cancelled mid-DNS.
    """

    def __init__(self, config: McpServerConfig):
        self.config = config
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self) -> McpConnection:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            if self.config.url:
                await self._connect_http(stack)
            else:
                await self._connect_stdio(stack)
            await self._session.initialize()
        except BaseException:
            await stack.__aexit__(None, None, None)
            raise
        self._stack = stack
        return self

    async def __aexit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        stack, self._stack = self._stack, None
        self._session = None
        if stack is None:
            return None
        return await stack.__aexit__(exc_type, exc, tb)

    async def _connect_stdio(self, stack: AsyncExitStack) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            raise RuntimeError("MCP SDK not installed. Install with: uv sync --extra mcp") from e

        if not self.config.command:
            raise ValueError("stdio MCP connection requires a 'command'")

        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args or [],
            env=self.config.env,
        )
        reader, writer = await stack.enter_async_context(stdio_client(server_params))
        self._session = await stack.enter_async_context(ClientSession(reader, writer))

    async def _connect_http(self, stack: AsyncExitStack) -> None:
        try:
            from mcp import ClientSession
        except ImportError as e:
            raise RuntimeError("MCP SDK not installed. Install with: uv sync --extra mcp") from e

        try:
            from mcp.client.sse import sse_client
        except ImportError as e:
            try:
                import mcp as _mcp

                installed = getattr(_mcp, "__version__", "unknown")
            except Exception:
                installed = "unknown"
            raise RuntimeError(
                f"Installed mcp SDK (version {installed}) does not expose "
                f"'mcp.client.sse.sse_client'; upgrade the mcp extra "
                f"(uv sync --extra mcp)."
            ) from e

        if not self.config.url:
            raise ValueError("HTTP/SSE MCP connection requires a 'url'")

        reader, writer = await stack.enter_async_context(
            sse_client(url=self.config.url, headers=self.config.headers or {})
        )
        self._session = await stack.enter_async_context(ClientSession(reader, writer))

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools on the server."""
        if not self._session:
            raise RuntimeError("Not connected to MCP server")

        response = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in response.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the server."""
        if not self._session:
            raise RuntimeError("Not connected to MCP server")

        result = await self._session.call_tool(tool_name, arguments)

        output: list[str] = []
        for item in result.content:
            if hasattr(item, "text"):
                output.append(item.text)
            elif hasattr(item, "type"):
                output.append(f"[{item.type}]")
            else:
                output.append(str(item))
        return {"content": output, "isError": result.isError}


# ---------------------------------------------------------------------------
# Connection strategies
# ---------------------------------------------------------------------------


class _PerCallStrategy:
    """Open a fresh session per call — preserves cancel-scope safety.

    This is character-equivalent to the pre-refactor ``invoke()`` body:
    ordering (stdio:enter → session:enter → session:initialize →
    session:call_tool → session:exit → stdio:exit), best-effort
    ``list_tools``, ExceptionGroup unwrapping.
    """

    def __init__(self, tool: "McpTool"):
        self._tool = tool

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self._tool
        try:
            async with McpConnection(tool._server_config) as connection:
                try:
                    tool._last_available_tools = await connection.list_tools()
                except Exception:
                    logger.debug("MCP list_tools failed", exc_info=True)
                return await connection.call_tool(tool_name, arguments)
        except BaseExceptionGroup as eg:  # noqa: F821
            inner = _unwrap_single_exception(eg)
            if isinstance(inner, Exception):
                raise inner from eg
            raise

    async def close(self) -> None:
        return None


class _PooledStrategy:
    """Reuse a single ``McpConnection`` across calls; drained by ``close()``.

    Serializes concurrent calls through ``_session_lock`` — MCP stdio is
    effectively single-stream, so a second connection would mean a
    second subprocess, defeating the point.

    Dead-session detection swaps on the *next* call, not inside the
    failing call. Swapping inside the failing call would keep us inside
    the dying session's cancel scope.
    """

    def __init__(self, tool: "McpTool"):
        self._tool = tool
        self._conn: McpConnection | None = None
        self._session_lock = asyncio.Lock()
        self._stale = False

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self._tool
        async with self._session_lock:
            if self._stale and self._conn is not None:
                try:
                    await self._conn.__aexit__(None, None, None)
                except Exception:
                    logger.debug("MCP pooled close after stale failed", exc_info=True)
                self._conn = None
                self._stale = False

            if self._conn is None:
                conn = McpConnection(tool._server_config)
                await conn.__aenter__()
                try:
                    tool._last_available_tools = await conn.list_tools()
                except Exception:
                    logger.debug("MCP pooled list_tools failed", exc_info=True)
                self._conn = conn

            try:
                return await self._conn.call_tool(tool_name, arguments)
            except BaseExceptionGroup as eg:  # noqa: F821
                self._stale = True
                inner = _unwrap_single_exception(eg)
                if isinstance(inner, Exception):
                    raise inner from eg
                raise
            except Exception:
                self._stale = True
                raise

    async def close(self) -> None:
        async with self._session_lock:
            conn, self._conn = self._conn, None
            self._stale = False
        if conn is None:
            return
        try:
            await conn.__aexit__(None, None, None)
        except Exception:
            logger.debug("MCP pooled close failed", exc_info=True)


# ---------------------------------------------------------------------------
# atexit sweep for pooled sessions — list of weakrefs so unhashable plugins
# (pydantic-backed BasePlugin) are tolerated.
# ---------------------------------------------------------------------------

_live_pools: "list[weakref.ref[McpTool]]" = []


def _register_live_pool(tool: "McpTool") -> None:
    # Opportunistically sweep dead refs.
    _live_pools[:] = [r for r in _live_pools if r() is not None]
    _live_pools.append(weakref.ref(tool))


def _atexit_drain_pools() -> None:
    tools = [r() for r in _live_pools]
    for tool in tools:
        if tool is None:
            continue
        strategy = getattr(tool, "_strategy", None)
        if not isinstance(strategy, _PooledStrategy):
            continue
        if strategy._conn is None:
            continue
        try:
            import asyncio as _asyncio

            loop = _asyncio.new_event_loop()
            try:
                loop.run_until_complete(strategy.close())
            finally:
                loop.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


atexit.register(_atexit_drain_pools)


# ---------------------------------------------------------------------------
# McpTool
# ---------------------------------------------------------------------------


class McpTool(TypedConfigPluginMixin, ToolPlugin):
    """Tool that forwards calls to an MCP server.

    What:
        Bridges to a Model Context Protocol server (stdio command or
        HTTP/SSE URL) and exposes the server's tools through this
        single ToolPlugin. ``invoke`` accepts ``{"tool": "<name>",
        "arguments": {...}}`` and forwards to the server. Optionally
        filters which tools are visible.

        ``connection_mode="per_call"`` (default): opens and closes a
        fresh stdio/SSE session per ``invoke()``. Preserves the
        anyio cancel-scope invariant that a dying subprocess cannot
        cancel the caller's next await.

        ``connection_mode="pooled"``: reuses one long-lived session
        across calls. Subsequent calls pay no subprocess-spawn cost.
        Trade-off: if the pooled session dies, its cancel scope may
        escape into the caller; we mitigate by swapping the session
        on the *next* call, not inside the failing call.

    Usage:
        ``{"id": "mcp_fs", "type": "mcp", "config": {"server":
        {"command": "python", "args": ["server.py"]}, "tools":
        ["read_file"], "connection_mode": "pooled"}}``.
        Requires ``uv sync --extra mcp``.

    Depends on:
        - the optional ``mcp`` Python SDK
        - an external MCP server reachable via stdio or HTTP/SSE
    """

    class Config(BaseModel):
        server: dict[str, Any] = Field(default_factory=dict)
        tools: list[str] = Field(default_factory=list)
        connection_mode: Literal["per_call", "pooled"] = "per_call"
        probe_on_preflight: bool = False
        dedup_inflight: bool = True

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._init_typed_config()

        server_config = self.cfg.server
        self._server_config = McpServerConfig(
            command=server_config.get("command"),
            args=server_config.get("args"),
            env=server_config.get("env"),
            url=server_config.get("url"),
            headers=server_config.get("headers"),
        )
        self._exposed_tools = set(self.cfg.tools)
        self._last_available_tools: list[dict[str, Any]] | None = None
        self._connection_mode = self.cfg.connection_mode
        self._probe_on_preflight = self.cfg.probe_on_preflight
        self._dedup_inflight = self.cfg.dedup_inflight
        self._inflight: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._inflight_lock = asyncio.Lock()

        if self._connection_mode == "pooled":
            self._strategy: _PerCallStrategy | _PooledStrategy = _PooledStrategy(self)
            _register_live_pool(self)
        else:
            self._strategy = _PerCallStrategy(self)

    async def preflight(self, context: Any) -> None:
        """Validate the mcp extra, server config, and (optionally) reachability.

        Runs once per session before the agent loop. Raises
        ``PermanentToolError`` on any misconfiguration so the runtime
        can surface it as a failed run before the LLM picks the tool.
        """
        tool_id = self.tool_name
        started = time.perf_counter()
        emit_event = self._emit_preflight_event_factory(context)

        try:
            import mcp  # noqa: F401
        except ImportError as e:
            msg = f"[tool:{tool_id}] mcp extra not installed; run: uv sync --extra mcp"
            await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
            raise PermanentToolError(msg, tool_name=tool_id) from e

        if self._server_config.url:
            parsed = urllib.parse.urlparse(self._server_config.url)
            if not parsed.scheme or not parsed.netloc:
                msg = (
                    f"[tool:{tool_id}] server.url '{self._server_config.url}' "
                    f"is not a valid URL (missing scheme or host)"
                )
                await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
                raise PermanentToolError(msg, tool_name=tool_id)
        else:
            cmd = self._server_config.command
            if not cmd:
                msg = f"[tool:{tool_id}] server config must set either 'command' (stdio) or 'url' (SSE/HTTP)"
                await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
                raise PermanentToolError(msg, tool_name=tool_id)
            if shutil.which(cmd) is None:
                msg = f"[tool:{tool_id}] stdio command '{cmd}' was not found on PATH"
                await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
                raise PermanentToolError(msg, tool_name=tool_id)

        tool_count: int | None = None
        if self._probe_on_preflight:
            try:
                async with McpConnection(self._server_config) as connection:
                    tools = await connection.list_tools()
                    tool_count = len(tools)
            except BaseExceptionGroup as eg:  # noqa: F821
                inner = _unwrap_single_exception(eg)
                msg = f"[tool:{tool_id}] preflight probe failed: {inner}"
                await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
                raise PermanentToolError(msg, tool_name=tool_id) from eg
            except Exception as exc:
                msg = f"[tool:{tool_id}] preflight probe failed: {exc}"
                await emit_event(result="error", error=msg, duration_ms=_ms_since(started))
                raise PermanentToolError(msg, tool_name=tool_id) from exc

        await emit_event(
            result="ok",
            duration_ms=_ms_since(started),
            tool_count=tool_count,
        )

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        """Forward tool call to MCP server.

        Routes through the configured connection strategy (``per_call``
        or ``pooled``) and, in per_call mode, coalesces concurrent
        identical calls when ``dedup_inflight`` is on.
        """
        tool_name = params.get("tool")
        if not tool_name:
            raise ValueError("'tool' parameter is required")
        if self._exposed_tools and tool_name not in self._exposed_tools:
            raise ValueError(f"Tool '{tool_name}' is not exposed by this MCP server")

        arguments = params.get("arguments", {}) or {}

        emit_events = _emit_call_events_factory(self, context)
        started = time.perf_counter()

        dedup_active = self._dedup_inflight and self._connection_mode == "per_call"

        if not dedup_active:
            await emit_events.connect()
            try:
                result = await self._strategy.call(tool_name, arguments)
            except Exception as exc:
                await emit_events.call_failed(tool_name, started, exc)
                raise
            await emit_events.call_ok(tool_name, started)
            return result

        key = (tool_name, _canonical_args_hash(arguments))

        async with self._inflight_lock:
            existing = self._inflight.get(key)
            if existing is not None:
                future_to_await = existing
                is_owner = False
            else:
                loop = asyncio.get_event_loop()
                future_to_await = loop.create_future()
                self._inflight[key] = future_to_await
                is_owner = True

        if not is_owner:
            return await future_to_await

        await emit_events.connect()
        try:
            result = await self._strategy.call(tool_name, arguments)
        except BaseException as exc:
            async with self._inflight_lock:
                self._inflight.pop(key, None)
            if not future_to_await.done():
                future_to_await.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
            if isinstance(exc, Exception):
                await emit_events.call_failed(tool_name, started, exc)
            raise

        async with self._inflight_lock:
            self._inflight.pop(key, None)
        if not future_to_await.done():
            future_to_await.set_result(result)
        await emit_events.call_ok(tool_name, started)
        return result

    async def close(self) -> None:
        """Drain the pooled session (if any). Idempotent."""
        strategy = self._strategy
        if isinstance(strategy, _PooledStrategy):
            await strategy.close()

    def get_available_tools(self) -> list[dict[str, Any]] | None:
        """Return tools observed on the most recent successful invoke()."""
        return self._last_available_tools

    # -- event helpers ------------------------------------------------------

    def _emit_preflight_event_factory(self, context: Any):
        bus = _get_event_bus(context)
        tool_id = self.tool_name

        async def emit(**payload: Any) -> None:
            if bus is None:
                return
            try:
                await bus.emit(
                    "tool.mcp.preflight",
                    tool_id=tool_id,
                    server=self._server_config.identifier(),
                    **{k: v for k, v in payload.items() if v is not None},
                )
            except Exception:  # pragma: no cover - event emission is best-effort
                logger.debug("MCP preflight event emission failed", exc_info=True)

        return emit


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _get_event_bus(context: Any) -> Any:
    if context is None:
        return None
    for attr in ("event_bus", "events"):
        bus = getattr(context, attr, None)
        if bus is not None and callable(getattr(bus, "emit", None)):
            return bus
    return None


class _CallEventsEmitter:
    def __init__(self, tool: McpTool, bus: Any) -> None:
        self._tool = tool
        self._bus = bus

    async def _emit(self, event: str, /, **payload: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(event, **payload)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("MCP event %s emission failed", event, exc_info=True)

    async def connect(self) -> None:
        await self._emit(
            "tool.mcp.connect",
            tool_id=self._tool.tool_name,
            server=self._tool._server_config.identifier(),
            mode=self._tool._connection_mode,
        )

    async def call_ok(self, tool_name: str, started: float) -> None:
        await self._emit(
            "tool.mcp.call",
            tool_id=self._tool.tool_name,
            tool_name=tool_name,
            success=True,
            duration_ms=_ms_since(started),
        )

    async def call_failed(self, tool_name: str, started: float, exc: BaseException) -> None:
        await self._emit(
            "tool.mcp.call",
            tool_id=self._tool.tool_name,
            tool_name=tool_name,
            success=False,
            duration_ms=_ms_since(started),
            error=str(exc),
        )


def _emit_call_events_factory(tool: McpTool, context: Any) -> _CallEventsEmitter:
    return _CallEventsEmitter(tool, _get_event_bus(context))
