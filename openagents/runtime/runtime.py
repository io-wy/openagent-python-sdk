"""Runtime entrypoint and orchestration flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config, load_config_dict
from openagents.config.schema import AgentDefinition, AppConfig
from openagents.errors.exceptions import ConfigError
from openagents.errors.suggestions import near_match
from openagents.interfaces.runtime import (
    RUN_STOP_FAILED,
    RunBudget,
    RunRequest,
    RunResult,
    RunStreamChunk,
    RunStreamChunkKind,
    StopReason,
)
from openagents.plugins.loader import load_agent_plugins, load_runtime_components
from openagents.runtime.stream_projection import project_event


class Runtime:
    """Main runtime entrypoint.

    Delegates to pluggable runtime/session/events/skills components loaded
    from config. When any of the top-level ``runtime``/``session``/
    ``events``/``skills`` fields is omitted from the config, pydantic
    schema defaults fill in the builtin references (``default``/
    ``in_memory``/``async``/``local``) and the plugin loader resolves them
    uniformly — there is no separate "defaults" path.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        _config_path: Path | None = None,
    ):
        self._config = config
        self._config_path = _config_path
        self._agents_by_id: dict[str, AgentDefinition] = {a.id: a for a in config.agents}
        self._session_plugins: dict[str, dict[str, Any]] = {}
        self._config_version: int = 0

        components = load_runtime_components(
            runtime_ref=config.runtime,
            session_ref=config.session,
            events_ref=config.events,
            skills_ref=config.skills,
            diagnostics_ref=config.diagnostics,
        )
        self._runtime = components.runtime
        self._session = components.session
        self._events = components.events
        self._skills = components.skills
        self._diagnostics = components.diagnostics

        self._maybe_auto_configure_logging(config)

    @staticmethod
    def _maybe_auto_configure_logging(config: AppConfig) -> None:
        """Opt-in hook: apply observability.configure() when the config requests it.

        Library etiquette: never auto-configure unless the config explicitly
        sets ``logging.auto_configure: true`` (or ``OPENAGENTS_LOG_AUTOCONFIGURE=1``
        overrides it).
        """
        from openagents.observability.config import merge_env_overrides
        from openagents.observability.logging import configure

        logging_cfg = config.logging
        if logging_cfg is None:
            # Still honor env-var-only activation.
            from openagents.observability.config import load_from_env

            env_cfg = load_from_env()
            if env_cfg is None or not env_cfg.auto_configure:
                return
            configure(env_cfg)
            return
        effective = merge_env_overrides(logging_cfg)
        if not effective.auto_configure:
            return
        configure(effective)

    @property
    def event_bus(self) -> Any:
        """Access the event bus instance."""
        return self._events

    @property
    def session_manager(self) -> Any:
        """Access the session manager instance."""
        return self._session

    @property
    def skills_manager(self) -> Any:
        """Access the host-level skills manager instance."""
        return self._skills

    @property
    def diagnostics(self) -> Any:
        """Access the diagnostics plugin instance."""
        return self._diagnostics

    async def _prepare_skills_for_session(self, session_id: str) -> None:
        prepare = getattr(self._skills, "prepare_session", None)
        if callable(prepare):
            await prepare(session_id=session_id, session_manager=self._session)

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

    def _unknown_agent_hint(self, agent_id: str) -> str:
        available = sorted(self._agents_by_id.keys())
        guess = near_match(agent_id, available)
        if guess:
            return f"Did you mean '{guess}'? Available agent ids: {available}"
        return f"Available agent ids: {available}"

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
                raise ConfigError(
                    f"Unknown agent id: '{agent_id}'",
                    hint=self._unknown_agent_hint(agent_id),
                )
            self._session_plugins[session_id][agent_id] = load_agent_plugins(agent)

        return self._session_plugins[session_id][agent_id]

    async def run(self, *, agent_id: str, session_id: str, input_text: str, deps: Any = None) -> Any:
        """Execute an agent run and return the legacy final output."""
        result = await self.run_detailed(
            request=RunRequest(
                agent_id=agent_id,
                session_id=session_id,
                input_text=input_text,
                budget=self._build_budget(agent_id),
                deps=deps,
            )
        )
        if result.stop_reason == RUN_STOP_FAILED:
            message = result.error_details.message if result.error_details is not None else "Agent run failed"
            raise RuntimeError(message)
        return result.final_output

    async def run_detailed(self, *, request: RunRequest) -> RunResult:
        """Execute an agent run and return structured runtime details.

        Delegates to ``self._runtime.run(...)``. ``RuntimePlugin.run`` must
        accept the keyword arguments ``request``, ``app_config``,
        ``agents_by_id``, ``agent_plugins`` and return a ``RunResult``.
        """
        agent = self._agents_by_id.get(request.agent_id)
        if agent is None:
            raise ConfigError(
                f"Unknown agent id: '{request.agent_id}'",
                hint=self._unknown_agent_hint(request.agent_id),
            )

        await self._prepare_skills_for_session(request.session_id)
        plugins = self._get_plugins_for_session(request.session_id, request.agent_id)
        result = await self._runtime.run(
            request=request,
            app_config=self._config,
            agents_by_id=self._agents_by_id,
            agent_plugins=plugins,
        )
        if not isinstance(result, RunResult):
            raise TypeError(
                f"RuntimePlugin.run must return RunResult, got "
                f"{type(result).__name__} from {type(self._runtime).__name__}"
            )
        return result

    async def run_stream(self, *, request: RunRequest):
        """Execute an agent run and yield RunStreamChunk events.

        Projects the event bus into a unified chunk stream, then yields a
        terminal ``RUN_FINISHED`` chunk carrying the final ``RunResult``.
        """
        import time

        # Subscribe a wildcard handler on the event bus and push projected chunks
        # into a queue. The run executes as a background task.
        queue: asyncio.Queue = asyncio.Queue()
        sequence = 0

        # Only project events for this run's run_id.
        def _make_handler():
            async def handler(event):
                nonlocal sequence
                payload = dict(event.payload or {})
                # Filter out events not tied to this run.
                run_id = payload.get("run_id")
                if run_id is not None and run_id != request.run_id:
                    return
                projected = project_event(event.name, payload)
                if projected is None:
                    return
                kind, data = projected
                sequence += 1
                chunk = RunStreamChunk(
                    kind=kind,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    agent_id=request.agent_id,
                    sequence=sequence,
                    timestamp_ms=int(time.time() * 1000),
                    payload=data,
                )
                await queue.put(chunk)

            return handler

        handler = _make_handler()
        self._events.subscribe("*", handler)

        # Mark the run as streaming so pattern.call_llm / call_tool can branch.
        request.context_hints = dict(request.context_hints or {})
        request.context_hints["__runtime_streaming__"] = True

        async def _drive_run():
            from openagents.interfaces.runtime import ErrorDetails as _ErrorDetails

            try:
                return await self.run_detailed(request=request)
            except Exception as exc:  # noqa: BLE001
                return RunResult(
                    run_id=request.run_id,
                    final_output=None,
                    stop_reason=StopReason.FAILED,
                    error_details=_ErrorDetails.from_exception(exc),
                )

        run_task = asyncio.create_task(_drive_run())

        try:
            while not run_task.done() or not queue.empty():
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.05)
                    yield chunk
                except asyncio.TimeoutError:
                    if run_task.done() and queue.empty():
                        break
            # Drain any remaining queued chunks.
            while not queue.empty():
                yield queue.get_nowait()

            result = await run_task
            sequence += 1
            yield RunStreamChunk(
                kind=RunStreamChunkKind.RUN_FINISHED,
                run_id=request.run_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                sequence=sequence,
                timestamp_ms=int(time.time() * 1000),
                result=result,
            )
        finally:
            if not run_task.done():
                run_task.cancel()
                try:
                    await run_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Best-effort unsubscribe (AsyncEventBus keeps a list; we remove by identity).
            try:
                subs = self._events._subscribers.get("*", [])
                if handler in subs:
                    subs.remove(handler)
            except (AttributeError, ValueError):
                pass

    def _build_budget(self, agent_id: str) -> RunBudget | None:
        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            return None
        return RunBudget(
            max_steps=agent.runtime.max_steps,
            max_duration_ms=agent.runtime.step_timeout_ms,
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
            raise ConfigError("Hot reload does not support changing top-level runtime/session/events.")

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
        if changed_agent_ids or removed_agent_ids:
            invalidate_mcp = getattr(self._runtime, "invalidate_mcp_pools_for_agents", None)
            if callable(invalidate_mcp):
                await invalidate_mcp(changed_agent_ids | removed_agent_ids)

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
            raise ConfigError(
                f"Unknown agent id: '{agent_id}'",
                hint=self._unknown_agent_hint(agent_id),
            )

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
            "tool_executor": {
                "type": agent.tool_executor.type if agent.tool_executor else None,
                "impl": agent.tool_executor.impl if agent.tool_executor else None,
            },
            "context_assembler": {
                "type": agent.context_assembler.type if agent.context_assembler else None,
                "impl": agent.context_assembler.impl if agent.context_assembler else None,
            },
            "tools": [t.id for t in agent.tools if t.enabled],
            "loaded_plugins": {
                "memory": type(plugins.memory).__name__ if plugins else None,
                "pattern": type(plugins.pattern).__name__ if plugins else None,
                "tool_executor": (type(plugins.tool_executor).__name__ if plugins and plugins.tool_executor else None),
                "context_assembler": (
                    type(plugins.context_assembler).__name__ if plugins and plugins.context_assembler else None
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
        await self.release_session(session_id)

    async def release_session(self, session_id: str) -> None:
        """Drop runtime-owned per-session resources (e.g. shared MCP pool).

        Lighter than :meth:`close_session`: leaves agent plugins alone but
        releases any runtime-level shared state (today: the MCP session
        pool) tied to ``session_id``. Idempotent.
        """
        release = getattr(self._runtime, "release_session", None)
        if callable(release):
            await release(session_id)

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

    def run_sync(self, *, agent_id: str, session_id: str, input_text: str, deps: Any = None) -> Any:
        """Synchronous wrapper for run()."""
        return asyncio.run(self.run(agent_id=agent_id, session_id=session_id, input_text=input_text, deps=deps))
