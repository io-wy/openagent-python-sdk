"""Session-scoped MCP pool + preflight dedup — ``DefaultRuntime`` internal.

Wires the existing ``ToolPlugin.preflight()`` hook and ``McpTool`` pooled
connection machinery into a per-session lifecycle owned by
``DefaultRuntime``. Not a public seam: no plugin registration, no
``runtime.config.*`` override hook, no cross-app reuse bar to clear.

Two concerns live here:

1. **Shared session pool** — one MCP process per
   ``(session_id, server_identifier)`` instead of one per
   ``McpTool`` instance. Multiple agents in the same session that point at
   the same MCP server share a single stdio/SSE session, serialised via a
   per-entry ``asyncio.Lock`` because MCP is single-stream.
2. **Preflight dedup** — once a tool's ``preflight()`` succeeds in a
   session, we skip it for every subsequent ``run_detailed`` on that
   session. Failures are *not* cached — a transient "command not on PATH"
   must be retryable after the user fixes their env.

Pools live on the runtime instance (``_mcp_coordinator._pools`` keyed by
session_id) and survive across runs on the same session. Teardown is
Phase 3: ``Runtime.release_session`` / ``Runtime.close`` /
``Runtime.reload`` invalidation, plus LRU/idle eviction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

from openagents.plugins.builtin.tool.mcp_tool import (
    McpConnection,
    McpServerConfig,
    McpTool,
)

logger = logging.getLogger(__name__)

SCRATCH_KEY = "__mcp_session_pool__"


@dataclass
class _SharedConnEntry:
    """One physical MCP session shared by N ``McpTool`` instances with matching identifier."""

    conn: McpConnection
    lock: asyncio.Lock
    tools_cache: list[dict[str, Any]] | None = None
    stale: bool = False


@dataclass
class _PreflightCacheEntry:
    ok: bool
    ts: float
    error: str | None = None


class _SessionMcpPool:
    """Per-session MCP resource bundle: shared conns + preflight cache.

    Lifetime matches ``DefaultRuntime._mcp_coordinator._pools[session_id]``;
    created on first ``run_detailed``, torn down on ``release_session`` /
    ``close_all`` / reload invalidation.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._conns: dict[str, _SharedConnEntry] = {}
        self._conn_creation_lock = asyncio.Lock()
        self._preflight_cache: dict[str, _PreflightCacheEntry] = {}
        self.last_used: float = time.monotonic()
        self._closed = False

    # -- shared conn plumbing ------------------------------------------------

    async def get_or_open_shared(
        self,
        identifier: str,
        server_config: McpServerConfig,
    ) -> _SharedConnEntry:
        """Return the shared conn for ``identifier``; open (or reopen if stale) as needed.

        Only the conn-creation step is serialised — the per-entry ``lock``
        is what serialises concurrent tool calls on an open conn. Opening
        a second identifier in parallel is fine.
        """
        self.last_used = time.monotonic()
        async with self._conn_creation_lock:
            entry = self._conns.get(identifier)
            if entry is not None and entry.stale:
                try:
                    await entry.conn.__aexit__(None, None, None)
                except Exception:
                    logger.debug(
                        "MCP session pool: stale conn close failed for %s",
                        identifier,
                        exc_info=True,
                    )
                self._conns.pop(identifier, None)
                entry = None
            if entry is None:
                conn = McpConnection(server_config)
                await conn.__aenter__()
                try:
                    tools = await conn.list_tools()
                except Exception:
                    tools = None
                    logger.debug(
                        "MCP session pool: list_tools failed for %s",
                        identifier,
                        exc_info=True,
                    )
                entry = _SharedConnEntry(conn=conn, lock=asyncio.Lock(), tools_cache=tools)
                self._conns[identifier] = entry
            return entry

    def mark_stale(self, identifier: str) -> None:
        entry = self._conns.get(identifier)
        if entry is not None:
            entry.stale = True

    # -- preflight cache -----------------------------------------------------

    def get_preflight_cache(self, tool_id: str) -> _PreflightCacheEntry | None:
        return self._preflight_cache.get(tool_id)

    def set_preflight_ok(self, tool_id: str) -> None:
        self._preflight_cache[tool_id] = _PreflightCacheEntry(ok=True, ts=time.monotonic())

    # -- bookkeeping ---------------------------------------------------------

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        """Drain all shared conns; idempotent."""
        if self._closed:
            return
        self._closed = True
        for identifier, entry in list(self._conns.items()):
            try:
                await entry.conn.__aexit__(None, None, None)
            except Exception:
                logger.debug(
                    "MCP session pool: close failed for %s",
                    identifier,
                    exc_info=True,
                )
        self._conns.clear()
        self._preflight_cache.clear()


class _McpSessionCoordinator:
    """``DefaultRuntime``-internal owner of per-session MCP pools.

    Eviction policies (both optional, both default off):

    - ``max_pooled_sessions`` — upper bound on the number of *live*
      session pools. When ``get_or_create`` is asked to create a new
      pool and the coordinator already holds ``max_pooled_sessions``
      pools, the least-recently-used one is closed first.
    - ``max_idle_seconds`` — an age ceiling on ``_SessionMcpPool.last_used``
      monotonic time. On every ``get_or_create`` the coordinator
      evicts any pool that hasn't been touched within this window
      before deciding whether to allocate a new one.

    Both eviction passes run under the same ``_pools_lock`` so there
    are no races between a new session entering and an old one leaving.
    """

    def __init__(
        self,
        *,
        max_pooled_sessions: int | None = None,
        max_idle_seconds: float | None = None,
    ) -> None:
        self._pools: dict[str, _SessionMcpPool] = {}
        self._pools_lock = asyncio.Lock()
        self._max_pooled_sessions = max_pooled_sessions
        self._max_idle_seconds = max_idle_seconds

    async def get_or_create(self, session_id: str) -> _SessionMcpPool:
        async with self._pools_lock:
            if self._max_idle_seconds is not None:
                await self._purge_idle_locked()
            pool = self._pools.get(session_id)
            if pool is None or pool.is_closed():
                if self._max_pooled_sessions is not None and len(self._pools) >= self._max_pooled_sessions:
                    await self._evict_lru_locked(exclude=session_id)
                pool = _SessionMcpPool(session_id)
                self._pools[session_id] = pool
            pool.touch()
            return pool

    async def _purge_idle_locked(self) -> None:
        """Drop pools whose ``last_used`` is older than ``max_idle_seconds``.

        Caller must ensure ``self._max_idle_seconds is not None`` — this
        method does not re-check the sentinel.
        """
        assert self._max_idle_seconds is not None
        cutoff = time.monotonic() - self._max_idle_seconds
        expired = [sid for sid, pool in self._pools.items() if pool.last_used < cutoff]
        for sid in expired:
            pool = self._pools.pop(sid, None)
            if pool is not None:
                await pool.close()

    async def _evict_lru_locked(self, *, exclude: str | None = None) -> None:
        """Close and drop the single least-recently-used pool.

        ``exclude`` protects a session id about to be created/refreshed
        in the same call — never evict the session we're serving.
        """
        candidates = [(pool.last_used, sid) for sid, pool in self._pools.items() if sid != exclude]
        if not candidates:
            return
        candidates.sort()
        _, lru_sid = candidates[0]
        pool = self._pools.pop(lru_sid, None)
        if pool is not None:
            await pool.close()

    def list_session_ids(self) -> list[str]:
        return list(self._pools.keys())

    async def warmup_eager(
        self,
        pool: _SessionMcpPool,
        tools: Iterable[Any],
    ) -> None:
        """Open the shared conn for every ``prelaunch=eager`` MCP tool.

        Errors propagate — warmup failures surface as ``PermanentToolError``
        at the preflight wiring layer and should terminate the run before
        ``pattern.execute`` would otherwise hit them mid-step.
        """
        for tool in tools:
            if not isinstance(tool, McpTool):
                continue
            if tool._prelaunch != "eager":
                continue
            identifier = tool._server_config.identifier()
            await pool.get_or_open_shared(identifier, tool._server_config)

    async def preflight_with_dedup(
        self,
        pool: _SessionMcpPool,
        tool: Any,
        tool_id: str,
    ) -> tuple[bool, BaseException | None]:
        """Run ``tool.preflight`` unless this session already cached a success.

        Returns ``(cached_hit, exception_or_None)``. ``cached_hit=True``
        means the callable was *not* invoked this run because a prior run
        on the same session passed. ``exception_or_None`` is the actual
        exception raised by ``tool.preflight`` on cache miss, or ``None``
        if it passed (or if the tool has no ``preflight`` method).

        Successful preflight results are cached for the lifetime of the
        pool; failures are *not* cached (retry-friendly).
        """
        cached = pool.get_preflight_cache(tool_id)
        if cached is not None and cached.ok:
            return (True, None)
        preflight_fn = getattr(tool, "preflight", None)
        if preflight_fn is None or not callable(preflight_fn):
            return (False, None)
        try:
            await preflight_fn(None)
        except BaseException as exc:  # noqa: BLE001
            return (False, exc)
        pool.set_preflight_ok(tool_id)
        return (False, None)

    async def release_session(self, session_id: str) -> None:
        async with self._pools_lock:
            pool = self._pools.pop(session_id, None)
        if pool is not None:
            await pool.close()

    async def close_all(self) -> None:
        async with self._pools_lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            await pool.close()
