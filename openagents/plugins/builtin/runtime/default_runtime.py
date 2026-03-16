"""Default runtime implementation - orchestrates agent execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK, supports
from openagents.interfaces.events import (
    CONTEXT_CREATED,
    EventBusPlugin,
    MEMORY_INJECTED,
    MEMORY_INJECT_FAILED,
    MEMORY_WRITEBACK_FAILED,
    MEMORY_WRITEBACK_SUCCEEDED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_REQUESTED,
    RUN_VALIDATED,
    SESSION_ACQUIRED,
)
from openagents.interfaces.pattern import ExecutionContext, PatternPlugin
from openagents.interfaces.runtime import RUNTIME_RUN, RuntimePlugin


class DefaultRuntime(RuntimePlugin):
    """Default runtime implementation.

    Orchestrates agent execution with:
    - Session isolation and locking
    - Event lifecycle management
    - Memory inject/execute/writeback flow
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(
            config=config or {},
            capabilities={RUNTIME_RUN},
        )
        self._event_bus: EventBusPlugin | None = None
        self._session_manager: Any | None = None
        self._llm_clients: dict[str, Any | None] = {}

    @property
    def event_bus(self) -> EventBusPlugin:
        if self._event_bus is None:
            raise RuntimeError("EventBus not initialized. Call load_runtime_components() first.")
        return self._event_bus

    @property
    def session_manager(self) -> Any:
        if self._session_manager is None:
            raise RuntimeError("SessionManager not initialized. Call load_runtime_components() first.")
        return self._session_manager

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        input_text: str,
        app_config: "AppConfig",
        agents_by_id: dict[str, "AgentDefinition"],
    ) -> Any:
        """Execute an agent run."""
        # Import here to avoid circular imports
        from openagents.llm.registry import create_llm_client
        from openagents.plugins.loader import load_agent_plugins

        agent = agents_by_id.get(agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent id: '{agent_id}'")

        await self._event_bus.emit(
            RUN_REQUESTED,
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
        )

        plugins = load_agent_plugins(agent)
        llm_client = self._get_llm_client(agent)
        await self._event_bus.emit(
            RUN_VALIDATED,
            agent_id=agent_id,
            session_id=session_id,
        )

        try:
            async with self._session_manager.session(session_id) as session_state:
                await self._event_bus.emit(
                    SESSION_ACQUIRED,
                    agent_id=agent_id,
                    session_id=session_id,
                )

                session_state.pop("_runtime_last_output", None)

                # Setup pattern with runtime data
                await plugins.pattern.setup(
                    agent_id=agent_id,
                    session_id=session_id,
                    input_text=input_text,
                    state=session_state,
                    tools=plugins.tools,
                    llm_client=llm_client,
                    llm_options=agent.llm,
                    event_bus=self._event_bus,
                )

                await self._event_bus.emit(
                    CONTEXT_CREATED,
                    agent_id=agent_id,
                    session_id=session_id,
                )

                # Run memory inject - pattern.context is now available
                await self._run_memory_inject(agent=agent, memory=plugins.memory, pattern=plugins.pattern)

                # Execute pattern
                result = await plugins.pattern.execute()

                # Run memory writeback
                await self._run_memory_writeback(agent=agent, memory=plugins.memory, pattern=plugins.pattern)

                await self._event_bus.emit(
                    RUN_COMPLETED,
                    agent_id=agent_id,
                    session_id=session_id,
                    result=result,
                )
                return result
        except Exception as exc:
            await self._event_bus.emit(
                RUN_FAILED,
                agent_id=agent_id,
                session_id=session_id,
                error=str(exc),
            )
            raise

    def _get_llm_client(self, agent: "AgentDefinition") -> Any | None:
        if agent.id in self._llm_clients:
            return self._llm_clients[agent.id]
        # Import here to avoid circular imports
        from openagents.llm.registry import create_llm_client
        client = create_llm_client(agent.llm)
        self._llm_clients[agent.id] = client
        return client

    async def _run_memory_inject(
        self,
        *,
        agent: AgentDefinition,
        memory: Any,
        pattern: PatternPlugin,
    ) -> None:
        if not supports(memory, MEMORY_INJECT):
            return
        context = pattern.context
        try:
            await memory.inject(context)
            await self._event_bus.emit(
                MEMORY_INJECTED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_INJECT_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            if agent.memory.on_error == "fail":
                raise

    async def _run_memory_writeback(
        self,
        *,
        agent: AgentDefinition,
        memory: Any,
        pattern: PatternPlugin,
    ) -> None:
        if not supports(memory, MEMORY_WRITEBACK):
            return
        context = pattern.context
        try:
            await memory.writeback(context)
            await self._event_bus.emit(
                MEMORY_WRITEBACK_SUCCEEDED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_WRITEBACK_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            if agent.memory.on_error == "fail":
                raise
