"""Local runner for the CoreCoder example without ``openagents.runtime.Runtime``.

This keeps the example transparent: load config, instantiate plugins, assemble
context, inject memory, execute the pattern, then persist transcript back into a
tiny in-process session store.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError
from openagents.interfaces.events import RuntimeEvent
from openagents.interfaces.runtime import ErrorDetails, RunBudget, RunRequest, RunResult, RunUsage, StopReason
from openagents.llm.registry import create_llm_client
from openagents.plugins.loader import LoadedAgentPlugins, load_agent_plugins


@dataclass
class _AgentBundle:
    agent: Any
    plugins: LoadedAgentPlugins
    llm_client: Any


@dataclass
class RunnerDeps:
    corecoder_runner: "CoreCoderLocalRunner"


class _SessionStore:
    """Small in-memory session store exposing the bits the assembler needs."""

    def __init__(self) -> None:
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._artifacts: dict[str, list[Any]] = {}

    async def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._messages.get(session_id, []))

    async def list_artifacts(self, session_id: str) -> list[Any]:
        return list(self._artifacts.get(session_id, []))

    def save(
        self,
        session_id: str,
        *,
        messages: list[dict[str, Any]],
        artifacts: list[Any],
    ) -> None:
        self._messages[session_id] = list(messages)
        self._artifacts[session_id] = list(artifacts)


class _NullEventBus:
    """No-op event bus used by the local runner."""

    def subscribe(self, event_name: str, handler: Any) -> None:
        _ = (event_name, handler)
        return None

    def unsubscribe(self, event_name: str, handler: Any) -> None:
        _ = (event_name, handler)
        return None

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        return RuntimeEvent(name=event_name, payload=dict(payload))

    async def get_history(
        self,
        event_name: str | None = None,
        limit: int | None = None,
    ) -> list[RuntimeEvent]:
        _ = (event_name, limit)
        return []

    async def clear_history(self) -> None:
        return None

    async def close(self) -> None:
        return None


class CoreCoderLocalRunner:
    """Manual runner for ``examples/corecoder_agent``.

    Avoids the default runtime black box while still reusing the example's
    pattern, memory, context assembler, tool plugins, and LLM providers.
    """

    def __init__(self, config_path: str | Path):
        self._config_path = Path(config_path)
        self._config = load_config(self._config_path)
        self._agents_by_id = {agent.id: agent for agent in self._config.agents}
        self._bundles: dict[str, _AgentBundle] = {}
        self._sessions = _SessionStore()
        self._event_bus = _NullEventBus()
        self._deps = RunnerDeps(corecoder_runner=self)

    def _ensure_bundle(self, agent_id: str) -> _AgentBundle:
        if agent_id in self._bundles:
            return self._bundles[agent_id]

        agent = self._agents_by_id.get(agent_id)
        if agent is None:
            raise ConfigError(
                f"Unknown agent id: '{agent_id}'",
                hint=f"Available agent ids: {sorted(self._agents_by_id)}",
            )
        if agent.llm is None:
            raise ConfigError(f"Agent '{agent_id}' has no llm configured")

        plugins = load_agent_plugins(agent)
        llm_client = create_llm_client(agent.llm)
        bundle = _AgentBundle(agent=agent, plugins=plugins, llm_client=llm_client)
        self._bundles[agent_id] = bundle
        return bundle

    def _default_budget(self, agent: Any) -> RunBudget:
        return RunBudget(
            max_steps=agent.runtime.max_steps,
            max_duration_ms=agent.runtime.step_timeout_ms,
            max_validation_retries=3,
        )

    async def _inject_memory(self, bundle: _AgentBundle, context: Any) -> None:
        memory = bundle.plugins.memory
        try:
            await memory.inject(context)
        except Exception:
            if getattr(bundle.agent.memory, "on_error", "fail") == "fail":
                raise

    async def _writeback_memory(self, bundle: _AgentBundle, context: Any) -> None:
        memory = bundle.plugins.memory
        try:
            await memory.writeback(context)
            await memory.compact(context)
        except Exception:
            if getattr(bundle.agent.memory, "on_error", "fail") == "fail":
                raise

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        input_text: str,
        budget: RunBudget | None = None,
    ) -> str:
        result = await self.run_detailed(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            budget=budget,
        )
        if result.stop_reason == StopReason.FAILED:
            message = result.error_details.message if result.error_details is not None else "Agent run failed"
            raise RuntimeError(message)
        return str(result.final_output or "")

    async def run_detailed(
        self,
        *,
        agent_id: str,
        session_id: str,
        input_text: str,
        budget: RunBudget | None = None,
    ) -> RunResult[str]:
        bundle = self._ensure_bundle(agent_id)
        request = RunRequest(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            budget=budget or self._default_budget(bundle.agent),
            deps=self._deps,
        )
        usage = RunUsage()
        state: dict[str, Any] = {}

        context_assembler = bundle.plugins.context_assembler
        session_state = {"llm_client": bundle.llm_client}
        if context_assembler is not None:
            assembly = await context_assembler.assemble(
                request=request,
                session_state=session_state,
                session_manager=self._sessions,
            )
            transcript = assembly.transcript
            session_artifacts = assembly.session_artifacts
            assembly_metadata = assembly.metadata
        else:
            transcript = await self._sessions.load_messages(session_id)
            session_artifacts = await self._sessions.list_artifacts(session_id)
            assembly_metadata = {}

        pattern = bundle.plugins.pattern
        await pattern.setup(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=bundle.plugins.tools,
            llm_client=bundle.llm_client,
            llm_options=bundle.agent.llm,
            event_bus=self._event_bus,
            transcript=transcript,
            session_artifacts=session_artifacts,
            assembly_metadata=assembly_metadata,
            run_request=request,
            tool_executor=bundle.plugins.tool_executor,
            usage=usage,
            artifacts=[],
        )

        context = pattern.context
        if context is None:
            raise RuntimeError("Pattern setup did not create a context")

        await self._inject_memory(bundle, context)

        try:
            final_output = await pattern.execute()
        except Exception as exc:
            return RunResult(
                run_id=request.run_id,
                final_output=None,
                stop_reason=StopReason.FAILED,
                usage=usage,
                artifacts=list(context.artifacts),
                error_details=ErrorDetails.from_exception(exc),
                metadata={"agent_id": agent_id, "session_id": session_id},
            )

        context.state["corecoder_summary"] = str(final_output or "").strip()
        await self._writeback_memory(bundle, context)

        self._sessions.save(
            session_id,
            messages=context.transcript,
            artifacts=[*list(context.session_artifacts), *list(context.artifacts)],
        )

        result = RunResult(
            run_id=request.run_id,
            final_output=str(final_output or ""),
            stop_reason=StopReason.COMPLETED,
            usage=usage,
            artifacts=list(context.artifacts),
            metadata={"agent_id": agent_id, "session_id": session_id},
        )
        if context_assembler is not None:
            finalized = await context_assembler.finalize(
                request=request,
                session_state=session_state,
                session_manager=self._sessions,
                result=result,
            )
            if finalized is not None:
                result = finalized
        return result

    async def close(self) -> None:
        for bundle in self._bundles.values():
            memory = getattr(bundle.plugins, "memory", None)
            if memory is not None and hasattr(memory, "close"):
                await memory.close()
        await self._event_bus.close()
