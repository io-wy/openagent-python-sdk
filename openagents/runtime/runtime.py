"""Runtime entrypoint and orchestration flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config
from openagents.config.schema import AgentDefinition, AppConfig
from openagents.errors.exceptions import ConfigError
from openagents.plugins.loader import load_agent_plugins, load_runtime_components


class Runtime:
    """Main runtime entrypoint.

    Delegates to pluggable runtime/session/events components loaded from config.
    """

    def __init__(
        self,
        config: AppConfig,
        _skip_plugin_load: bool = False,  # Internal: skip for backward compat
    ):
        self._config = config
        self._agents_by_id: dict[str, AgentDefinition] = {a.id: a for a in config.agents}
        # Per-session agent plugins: {session_id: {agent_id: plugins}}
        self._session_plugins: dict[str, dict[str, Any]] = {}
        # Config version for atomic swap
        self._config_version: int = 0

        if _skip_plugin_load:
            # Backward compatibility mode - use builtins directly
            from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
            from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime
            from openagents.plugins.builtin.session.in_memory import InMemorySessionManager

            self._events = AsyncEventBus()
            self._session = InMemorySessionManager()
            self._runtime = DefaultRuntime(
                config={},
                event_bus=self._events,
                session_manager=self._session,
            )
        else:
            # Load plugins from config
            components = load_runtime_components(
                runtime_ref=config.runtime,
                session_ref=config.session,
                events_ref=config.events,
            )
            self._runtime = components.runtime
            self._session = components.session
            self._events = components.events

    @property
    def event_bus(self) -> Any:
        """Access the event bus instance."""
        return self._events

    @property
    def session_manager(self) -> Any:
        """Access the session manager instance."""
        return self._session

    @classmethod
    def from_config(cls, config_path: str | Path) -> "Runtime":
        return cls(load_config(config_path))

    def _get_plugins_for_session(self, session_id: str, agent_id: str) -> Any:
        """Get or create plugins for a specific session.

        Each session gets its own set of plugins, enabling hot reload
        without affecting in-flight requests.
        """
        if session_id not in self._session_plugins:
            self._session_plugins[session_id] = {}

        if agent_id not in self._session_plugins[session_id]:
            agent = self._agents_by_id.get(agent_id)
            if agent is None:
                raise ConfigError(f"Unknown agent id: '{agent_id}'")
            self._session_plugins[session_id][agent_id] = load_agent_plugins(agent)

        return self._session_plugins[session_id][agent_id]

    async def run(self, *, agent_id: str, session_id: str, input_text: str) -> Any:
        """Execute an agent run."""
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            raise ConfigError(f"Unknown agent id: '{agent_id}'")

        # Get session-specific plugins (isolated for hot reload)
        plugins = self._get_plugins_for_session(session_id, agent_id)

        # Delegate to the runtime plugin
        return await self._runtime.run(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            app_config=self._config,
            agents_by_id=self._agents_by_id,
            agent_plugins=plugins,
        )

    async def reload(self) -> None:
        """Atomic hot reload - swap config version, keep sessions running.

        New sessions will use the new config.
        Running sessions keep their existing plugins until they complete.
        """
        old_version = self._config_version
        self._config_version += 1

        # Reload config
        # Note: In production, we'd watch the file and reload on change
        # This method can be called externally or by a file watcher

        # Emit reload event
        await self._events.emit(
            "config.reloaded",
            old_version=old_version,
            new_version=self._config_version,
        )

    async def reload_agent(self, agent_id: str) -> None:
        """Reload a specific agent's plugins.

        This will be used for new sessions. Existing sessions keep their plugins.
        """
        if agent_id not in self._agents_by_id:
            raise ConfigError(f"Unknown agent id: '{agent_id}'")

        # Clear cached plugins for all sessions (new sessions will get fresh ones)
        for session_plugins in self._session_plugins.values():
            if agent_id in session_plugins:
                old_plugins = session_plugins[agent_id]
                # Close old plugins if they have close method
                if hasattr(old_plugins.memory, "close"):
                    await old_plugins.memory.close()
                del session_plugins[agent_id]

        await self._events.emit(
            "agent.reloaded",
            agent_id=agent_id,
        )

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self._session_plugins)

    async def get_agent_info(self, agent_id: str) -> dict[str, Any] | None:
        """Get information about an agent."""
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            return None

        # Get plugins from any session (they should all be the same)
        plugins = None
        for session_plugins in self._session_plugins.values():
            if agent_id in session_plugins:
                plugins = session_plugins[agent_id]
                break

        return {
            "id": agent.id,
            "name": agent.name,
            "memory": {
                "type": agent.memory.type,
                "impl": agent.memory.impl,
            },
            "pattern": {
                "type": agent.pattern.type,
                "impl": agent.pattern.impl,
            },
            "tools": [t.id for t in agent.tools if t.enabled],
            "loaded_plugins": {
                "memory": type(plugins.memory).__name__ if plugins else None,
                "pattern": type(plugins.pattern).__name__ if plugins else None,
                "tools": list(plugins.tools.keys()) if plugins else [],
            },
        }

    async def close_session(self, session_id: str) -> None:
        """Close a specific session and cleanup its plugins."""
        if session_id in self._session_plugins:
            for agent_id, plugins in self._session_plugins[session_id].items():
                if hasattr(plugins.memory, "close"):
                    await plugins.memory.close()
            del self._session_plugins[session_id]

    async def close(self) -> None:
        """Cleanup runtime resources."""
        # Close all session plugins
        for session_plugins in self._session_plugins.values():
            for agent_id, plugins in session_plugins.items():
                if hasattr(plugins.memory, "close"):
                    await plugins.memory.close()

        if hasattr(self._runtime, "close"):
            await self._runtime.close()
        if hasattr(self._session, "close"):
            await self._session.close()
        if hasattr(self._events, "close"):
            await self._events.close()

    def run_sync(self, *, agent_id: str, session_id: str, input_text: str) -> Any:
        """Synchronous wrapper for run()."""
        return asyncio.run(self.run(agent_id=agent_id, session_id=session_id, input_text=input_text))
