"""Zep-backed long-term memory plugin.

Depends on the optional ``zep-python`` package and a running Zep server
(cloud or self-hosted).  Falls back to a local file-backed store when Zep
is unreachable so development / CI doesn't require an external service.

Usage::
    {"type": "zep", "config": {
        "api_key": "zep-api-key",
        "api_url": "https://api.getzep.com",
        "collection": "openagents",
        "search_limit": 5,
    }}
"""

from __future__ import annotations

from datetime import datetime, timezone

from openagents.interfaces.memory import MemoryPlugin


class ZepMemory(MemoryPlugin):
    """Long-term memory backed by Zep, with local fallback.

    What:
        Persists agent interactions as Zep sessions so knowledge accumulates
        across runs.  Injects relevant context into ``context.memory_view``
        before each run.  Writeback appends new interactions.

    Fallback:
        When ``zep-python`` is not installed or the Zep server is unreachable,
        stores entries under ``memory_dir`` as JSON (one file per session).

    Config:
        api_key: Zep API key (cloud or self-hosted).
        api_url: Zep server URL (default: ``http://localhost:8000``).
        collection: Logical namespace (default: ``openagents``).
        search_limit: Max entries returned by ``retrieve()`` (default: 5).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._api_key: str | None = self.config.get("api_key")
        self._api_url: str = self.config.get("api_url", "http://localhost:8000")
        self._collection: str = self.config.get("collection", "openagents")
        self._search_limit: int = int(self.config.get("search_limit", 5))
        self._client: Any = None  # Lazy Zep client
        self._fallback: Any = None  # Local fallback store

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    async def inject(self, context: Any) -> None:
        """Retrieve relevant Zep memories and inject them into context."""
        session_id = self._session_id(context)
        if not session_id:
            return

        entries: list[dict[str, Any]] = []
        try:
            client = await self._get_client()
            if client is not None:
                result = await client.memory.get(session_id)
                if result and result.messages:
                    for msg in result.messages[-self._search_limit :]:
                        entries.append({
                            "role": getattr(msg, "role_type", getattr(msg, "role", "")),
                            "content": getattr(msg, "content", str(msg)),
                            "source": "zep",
                        })
            else:
                entries = await self._fallback_retrieve(session_id)
        except Exception:
            entries = await self._fallback_retrieve(session_id)

        if entries:
            context.memory_view.setdefault("zep_memory", []).extend(entries)

    async def writeback(self, context: Any) -> None:
        """Persist new interactions to Zep."""
        session_id = self._session_id(context)
        if not session_id:
            return

        # Collect from pending writes (same protocol as MarkdownMemory)
        pending = context.state.get("_pending_memory_writes") or []
        if not pending:
            return

        try:
            client = await self._get_client()
            if client is not None:
                messages = []
                for entry in pending:
                    messages.append(
                        _make_message(
                            role=entry.get("role", "system"),
                            content=entry.get("content", str(entry)),
                        )
                    )
                await client.memory.add(session_id, messages=messages)
            else:
                await self._fallback_write(session_id, pending)
        except Exception:
            await self._fallback_write(session_id, pending)

        context.state["_pending_memory_writes"] = []

    async def retrieve(
        self, query: str, context: Any
    ) -> list[dict[str, Any]]:
        """Search Zep for entries matching *query*."""
        session_id = self._session_id(context)
        if not session_id:
            return []

        try:
            client = await self._get_client()
            if client is not None:
                result = await client.memory.get(session_id)
                if not result or not result.messages:
                    return []
                q = query.lower()
                return [
                    {
                        "role": getattr(m, "role_type", getattr(m, "role", "")),
                        "content": getattr(m, "content", str(m)),
                        "source": "zep",
                    }
                    for m in result.messages
                    if q in getattr(m, "content", "").lower()
                ][: self._search_limit]
        except Exception:
            pass

        return await self._fallback_search(query, session_id)

    async def compact(self, context: Any) -> None:
        """No-op — Zep manages summarization server-side."""

    async def close(self) -> None:
        self._client = None
        self._fallback = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_client(self) -> Any | None:
        """Lazy-init the Zep client. Returns None when unavailable."""
        if self._client is not None:
            return self._client

        try:
            from zep_python import Zep

            self._client = Zep(
                api_key=self._api_key or "",
                api_url=self._api_url,
            )
            # Quick connectivity check
            await self._client.memory.get("__health__")
        except Exception:
            self._client = None
        return self._client

    @staticmethod
    def _session_id(context: Any) -> str:
        sid = getattr(context, "session_id", None)
        if sid:
            return str(sid)
        return ""

    # ---- fallback (local JSON files) -------------------------------

    async def _fallback_retrieve(self, session_id: str) -> list[dict[str, Any]]:
        store = await self._get_fallback()
        return store.get(session_id, [])[-self._search_limit :]

    async def _fallback_write(
        self, session_id: str, entries: list[dict[str, Any]]
    ) -> None:
        store = await self._get_fallback()
        records = store.setdefault(session_id, [])
        for entry in entries:
            records.append({
                "role": entry.get("role", "system"),
                "content": entry.get("content", str(entry)),
                "ts": datetime.now(timezone.utc).isoformat(),
            })

    async def _fallback_search(
        self, query: str, session_id: str
    ) -> list[dict[str, Any]]:
        store = await self._get_fallback()
        records = store.get(session_id, [])
        q = query.lower()
        return [r for r in records if q in r.get("content", "").lower()][
            : self._search_limit
        ]

    async def _get_fallback(self) -> dict[str, list[dict[str, Any]]]:
        """Lazy-load the local fallback store from a JSON file."""
        if self._fallback is not None:
            return self._fallback

        import json
        import os
        from pathlib import Path

        memory_dir = Path(
            self.config.get("memory_dir", "~/.config/openagents/memory/zep")
        ).expanduser()
        memory_dir.mkdir(parents=True, exist_ok=True)
        path = memory_dir / f"{self._collection}.json"

        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._fallback = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._fallback = {}
        else:
            self._fallback = {}

        # Auto-persist on write
        store = self._fallback

        class _AutoPersist(dict):
            """Dict subclass that auto-saves on mutation."""

            def __setitem__(self, k, v):
                super().__setitem__(k, v)
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(store, f, ensure_ascii=False, indent=2)
                except OSError:
                    pass

        self._fallback = _AutoPersist(store)
        return self._fallback


def _make_message(role: str, content: str) -> Any:
    """Build a Zep Message object, falling back to a plain dict."""
    try:
        from zep_python.memory import Message

        return Message(role=role, role_type=role, content=content)
    except Exception:
        return {"role": role, "role_type": role, "content": content}
