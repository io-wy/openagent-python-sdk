"""Mem0-based memory plugin using agent's LLM."""

from __future__ import annotations

import logging
from typing import Any

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin

logger = logging.getLogger(__name__)


class Mem0Memory(MemoryPlugin):
    """Semantic memory using Mem0 with agent's LLM.

    What:
        Uses the LLM configured on the agent for embeddings and memory
        operations via the optional ``mem0ai`` package. No additional
        API keys required - reuses the agent's LLM client.

    Usage:
        ``{"type": "mem0", "config": {"collection_name":
        "openagents_memory", "search_limit": 5}}``. Requires the
        ``mem0`` extra: ``uv sync --extra mem0``.

    Depends on:
        - the optional ``mem0ai`` PyPI package
        - the agent's configured LLM client (passed in via context)
        - ``RunContext.memory_view`` / ``RunContext.input_text``
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={MEMORY_INJECT, MEMORY_WRITEBACK},
        )
        self._collection_name = self.config.get("collection_name", "openagents_memory")
        self._search_limit = self.config.get("search_limit", 5)

    async def inject(self, context: Any) -> None:
        """Search for relevant memories and inject into context."""
        try:
            # Try to use Mem0 if available
            from mem0 import Memory

            # Get LLM config from agent
            llm_config = getattr(context, "llm_options", None)
            if llm_config is None:
                context.memory_view["mem0_history"] = []
                context.memory_view["history"] = []
                return

            # Create Mem0 client using agent's LLM config
            mem0_config = {
                "provider": getattr(llm_config, "provider", "openai"),
            }

            # Get API key from LLM config or env
            api_key = getattr(llm_config, "api_key", None)
            if not api_key and hasattr(llm_config, "api_key_env"):
                import os

                api_key_env = getattr(llm_config, "api_key_env")
                if api_key_env:
                    api_key = os.environ.get(api_key_env)

            if api_key:
                mem0_config["api_key"] = api_key

            model = getattr(llm_config, "model", None)
            if model:
                mem0_config["model"] = model

            client = Memory.from_config(mem0_config)

            # Search for relevant memories
            query = context.input_text
            results = await self._search_memories(client, query, self._search_limit)

            context.memory_view["mem0_history"] = results
            context.memory_view["history"] = [r.get("memory", "") for r in results]

        except ImportError:
            # mem0 not installed
            context.memory_view["mem0_history"] = []
            context.memory_view["history"] = []
        except Exception:
            # On any error, fallback to empty
            context.memory_view["mem0_history"] = []
            context.memory_view["history"] = []

    async def compact(self, context: Any) -> None:
        """No-op: Mem0 handles compaction internally."""

    async def writeback(self, context: Any) -> None:
        """Store current interaction in Mem0."""
        try:
            from mem0 import Memory

            llm_config = getattr(context, "llm_options", None)
            if llm_config is None:
                return

            mem0_config = {
                "provider": getattr(llm_config, "provider", "openai"),
            }

            api_key = getattr(llm_config, "api_key", None)
            if not api_key and hasattr(llm_config, "api_key_env"):
                import os

                api_key_env = getattr(llm_config, "api_key_env")
                if api_key_env:
                    api_key = os.environ.get(api_key_env)

            if api_key:
                mem0_config["api_key"] = api_key

            model = getattr(llm_config, "model", None)
            if model:
                mem0_config["model"] = model

            client = Memory.from_config(mem0_config)

            # Prepare memory content
            memory_content = context.input_text
            if context.tool_results:
                tool_info = ", ".join(
                    f"{r.get('tool_id', 'unknown')}: {r.get('result', r.get('error', 'error'))}"
                    for r in context.tool_results
                )
                memory_content = f"{memory_content}\n[Tools: {tool_info}]"

            if context.state.get("_runtime_last_output"):
                output = context.state["_runtime_last_output"]
                memory_content = f"{memory_content}\n[Output: {output}]"

            await self._add_memory(client, memory_content, context.session_id)

        except Exception:
            logger.warning("Error writing memory to Mem0", exc_info=True)

    async def _search_memories(self, client, query: str, limit: int) -> list:
        import asyncio

        def search():
            return client.search(query=query, limit=limit)

        return await asyncio.get_event_loop().run_in_executor(None, search)

    async def _add_memory(self, client, content: str, session_id: str) -> None:
        import asyncio

        def add():
            return client.add(
                memories=[content],
                metadata={"session_id": session_id},
            )

        await asyncio.get_event_loop().run_in_executor(None, add)
