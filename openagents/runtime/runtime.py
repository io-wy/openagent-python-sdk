"""Runtime entrypoint and orchestration flow."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config, load_config_dict
from openagents.config.schema import AgentDefinition, AppConfig
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.runtime import RUN_STOP_FAILED, RunBudget, RunRequest, RunResult
from openagents.plugins.loader import load_agent_plugins, load_runtime_components


class Runtime:
    """Main runtime entrypoint.

    Delegates to pluggable runtime/session/events components loaded from config.
    """

    def __init__(
        self,
        config: AppConfig,
        _skip_plugin_load: bool = False,
        _config_path: Path | None = None,
    ):
        self._config = config
        self._config_path = _config_path
        self._agents_by_id: dict[str, AgentDefinition] = {a.id: a for a in config.agents}
        self._session_plugins: dict[str, dict[str, Any]] = {}
        self._config_version: int = 0

        if _skip_plugin_load:
            from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
            from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime
            from openagents.plugins.builtin.session.in_memory import InMemorySessionManager

            self._events = AsyncEventBus()
            self._session = InMemorySessionManager()
            self._runtime = DefaultRuntime(config={})
            self._runtime._event_bus = self._events
            self._runtime._session_manager = self._session
        else:
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
        path = Path(config_path)
        config = load_config(path)
        return cls(config, _config_path=path)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Runtime":
        """Create a runtime directly from a Python config dict."""
        config = load_config_dict(payload)
        return cls(config)

    async def _invalidate_runtime_agent_cache(self, agent_ids: set[str] | None = None) -> None:
        """Invalidate runtime-level per-agent caches when config changes."""
        invalidate = getattr(self._runtime, "invalidate_llm_client", None)
        if not callable(invalidate):
            return
        if agent_ids is None:
            await invalidate()
            return
        for agent_id in agent_ids:
            await invalidate(agent_id)

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
        """Execute an agent run and return the legacy final output."""
        result = await self.run_detailed(
            request=RunRequest(
                agent_id=agent_id,
                session_id=session_id,
                input_text=input_text,
                budget=self._build_budget(agent_id),
            )
        )
        if result.exception is not None:
            raise result.exception
        if result.stop_reason == RUN_STOP_FAILED:
            raise RuntimeError(result.error or "Agent run failed")
        return result.final_output

    async def run_detailed(self, *, request: RunRequest) -> RunResult:
        """Execute an agent run and return structured runtime details."""
        agent = self._agents_by_id.get(request.agent_id)
        if agent is None:
            raise ConfigError(f"Unknown agent id: '{request.agent_id}'")

        plugins = self._get_plugins_for_session(request.session_id, request.agent_id)
        return await self._run_runtime(request=request, plugins=plugins)

    def _build_budget(self, agent_id: str) -> RunBudget | None:
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            return None
        return RunBudget(
            max_steps=agent.runtime.max_steps,
            max_duration_ms=agent.runtime.step_timeout_ms,
        )

    async def _run_runtime(self, *, request: RunRequest, plugins: Any) -> RunResult:
        run_signature = inspect.signature(self._runtime.run).parameters
        supports_request = (
            "request" in run_signature
            or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in run_signature.values())
        )
        if supports_request:
            result = await self._runtime.run(
                request=request,
                app_config=self._config,
                agents_by_id=self._agents_by_id,
                agent_plugins=plugins,
            )
        else:
            result = await self._runtime.run(
                agent_id=request.agent_id,
                session_id=request.session_id,
                input_text=request.input_text,
                app_config=self._config,
                agents_by_id=self._agents_by_id,
                agent_plugins=plugins,
            )

        if isinstance(result, RunResult):
            return result
        return RunResult(
            run_id=request.run_id,
            final_output=result,
            metadata={
                "agent_id": request.agent_id,
                "session_id": request.session_id,
            },
        )

    async def reload(self) -> None:
        """Reload agent config from disk for future sessions.

        Existing session plugin instances remain untouched. Top-level runtime,
        session, and event bus components are not hot-swapped.
        """
        if self._config_path is None:
            raise ConfigError("Cannot reload: no config path available")

        new_config = load_config(self._config_path)
        if (
            new_config.runtime != self._config.runtime
            or new_config.session != self._config.session
            or new_config.events != self._config.events
        ):
            raise ConfigError(
                "Hot reload does not support changing top-level runtime/session/events."
            )

        old_version = self._config_version
        old_agents = {agent.id: agent for agent in self._config.agents}
        new_agents = {agent.id: agent for agent in new_config.agents}
        changed_agent_ids = {
            agent_id
            for agent_id in old_agents.keys() | new_agents.keys()
            if old_agents.get(agent_id) != new_agents.get(agent_id)
        }
        removed_agent_ids = set(old_agents) - set(new_agents)

        self._config_version += 1
        self._config = new_config
        self._agents_by_id = new_agents
        await self._invalidate_runtime_agent_cache(changed_agent_ids)

        for session_plugins in self._session_plugins.values():
            for agent_id in removed_agent_ids:
                plugins = session_plugins.pop(agent_id, None)
                if plugins is not None and hasattr(plugins.memory, "close"):
                    await plugins.memory.close()

        await self._events.emit(
            "config.reloaded",
            old_version=old_version,
            new_version=self._config_version,
            changed_agents=sorted(changed_agent_ids),
        )

    async def reload_agent(self, agent_id: str) -> None:
        """Reload a specific agent's plugins.

        This will be used for new sessions. Existing sessions keep their plugins.
        """
        if agent_id not in self._agents_by_id:
            raise ConfigError(f"Unknown agent id: '{agent_id}'")

        for session_plugins in self._session_plugins.values():
            if agent_id in session_plugins:
                old_plugins = session_plugins[agent_id]
                if hasattr(old_plugins.memory, "close"):
                    await old_plugins.memory.close()
                del session_plugins[agent_id]

        await self._invalidate_runtime_agent_cache({agent_id})

        await self._events.emit(
            "agent.reloaded",
            agent_id=agent_id,
        )

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self._session_plugins)

    async def list_agents(self) -> list[dict[str, Any]]:
        """List all available agents."""
        return [
            {
                "id": agent.id,
                "name": agent.name,
            }
            for agent in self._agents_by_id.values()
        ]

    async def get_agent_info(self, agent_id: str) -> dict[str, Any] | None:
        """Get information about an agent."""
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            return None

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
            "skill": {
                "type": agent.skill.type if agent.skill else None,
                "impl": agent.skill.impl if agent.skill else None,
            },
            "tool_executor": {
                "type": agent.tool_executor.type if agent.tool_executor else None,
                "impl": agent.tool_executor.impl if agent.tool_executor else None,
            },
            "execution_policy": {
                "type": agent.execution_policy.type if agent.execution_policy else None,
                "impl": agent.execution_policy.impl if agent.execution_policy else None,
            },
            "context_assembler": {
                "type": agent.context_assembler.type if agent.context_assembler else None,
                "impl": agent.context_assembler.impl if agent.context_assembler else None,
            },
            "followup_resolver": {
                "type": agent.followup_resolver.type if agent.followup_resolver else None,
                "impl": agent.followup_resolver.impl if agent.followup_resolver else None,
            },
            "response_repair_policy": {
                "type": (
                    agent.response_repair_policy.type
                    if agent.response_repair_policy
                    else None
                ),
                "impl": (
                    agent.response_repair_policy.impl
                    if agent.response_repair_policy
                    else None
                ),
            },
            "tools": [t.id for t in agent.tools if t.enabled],
            "loaded_plugins": {
                "memory": type(plugins.memory).__name__ if plugins else None,
                "pattern": type(plugins.pattern).__name__ if plugins else None,
                "skill": type(plugins.skill).__name__ if plugins and plugins.skill else None,
                "tool_executor": (
                    type(plugins.tool_executor).__name__
                    if plugins and plugins.tool_executor
                    else None
                ),
                "execution_policy": (
                    type(plugins.execution_policy).__name__
                    if plugins and plugins.execution_policy
                    else None
                ),
                "context_assembler": (
                    type(plugins.context_assembler).__name__
                    if plugins and plugins.context_assembler
                    else None
                ),
                "followup_resolver": (
                    type(plugins.followup_resolver).__name__
                    if plugins and plugins.followup_resolver
                    else None
                ),
                "response_repair_policy": (
                    type(plugins.response_repair_policy).__name__
                    if plugins and plugins.response_repair_policy
                    else None
                ),
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
