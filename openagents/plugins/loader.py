"""Plugin loader and capability checks."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from openagents.config.schema import (
    AgentDefinition,
    ContextAssemblerRef,
    ExecutionPolicyRef,
    EventBusRef,
    MemoryRef,
    PatternRef,
    PluginRef,
    RuntimeRef,
    SkillRef,
    SessionRef,
    ToolExecutorRef,
    ToolRef,
)
from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    PATTERN_EXECUTE,
    PATTERN_REACT,
    SKILL_CONTEXT_AUGMENT,
    SKILL_METADATA,
    SKILL_POST_RUN,
    SKILL_PRE_RUN,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOL_FILTER,
    SKILL_TOOLS,
    TOOL_INVOKE,
    normalize_capabilities,
)
from openagents.interfaces.runtime import RUNTIME_RUN
from openagents.interfaces.session import SESSION_MANAGE
from openagents.interfaces.events import EVENT_EMIT, EVENT_SUBSCRIBE
from openagents.errors.exceptions import CapabilityError, PluginLoadError
from openagents.plugins.registry import get_builtin_plugin_class


@dataclass
class LoadedAgentPlugins:
    memory: Any
    pattern: Any
    skill: Any | None
    tool_executor: Any | None
    execution_policy: Any | None
    context_assembler: Any | None
    tools: dict[str, Any]


@dataclass
class LoadedRuntimeComponents:
    runtime: Any
    session: Any
    events: Any


def _import_symbol(path: str) -> Any:
    if "." not in path:
        raise PluginLoadError(f"Invalid impl path: '{path}'")
    module_name, attr_name = path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - defensive
        raise PluginLoadError(f"Failed to import module '{module_name}'") from exc
    try:
        return getattr(module, attr_name)
    except AttributeError as exc:
        raise PluginLoadError(f"Module '{module_name}' has no symbol '{attr_name}'") from exc


def _instantiate(factory: Any, config: dict[str, Any]) -> Any:
    if not callable(factory):
        return factory
    for call in (
        lambda: factory(config=config),
        lambda: factory(config),
        lambda: factory(),
    ):
        try:
            return call()
        except TypeError:
            continue
    raise PluginLoadError(f"Could not instantiate plugin from {factory!r}")


def _load_plugin(kind: str, ref: PluginRef) -> Any:
    # impl takes priority if provided
    if ref.impl:
        symbol = _import_symbol(ref.impl)
        return _instantiate(symbol, ref.config)
    # Fall back to type (builtin or decorator-registered)
    if ref.type:
        plugin_cls = get_builtin_plugin_class(kind, ref.type)
        if plugin_cls is None:
            raise PluginLoadError(f"Unknown {kind} plugin type: '{ref.type}'")
        return _instantiate(plugin_cls, ref.config)
    raise PluginLoadError(f"{kind} plugin must set one of 'type' or 'impl'")


def _capability_set(plugin: Any) -> set[str]:
    return normalize_capabilities(getattr(plugin, "capabilities", set()))


def _validate_method_for_capability(plugin: Any, capability: str, method_name: str) -> None:
    capabilities = _capability_set(plugin)
    if capability in capabilities and not callable(getattr(plugin, method_name, None)):
        raise CapabilityError(
            f"Plugin '{type(plugin).__name__}' declares '{capability}' "
            f"but does not implement '{method_name}'"
        )


def _validate_required_capabilities(
    plugin: Any,
    required: set[str],
    where: str,
) -> None:
    missing = required - _capability_set(plugin)
    if missing:
        raise CapabilityError(
            f"{where} is missing required capabilities: {sorted(missing)}"
        )


def load_memory_plugin(ref: MemoryRef) -> Any:
    plugin = _load_plugin("memory", ref)
    _validate_method_for_capability(plugin, MEMORY_INJECT, "inject")
    _validate_method_for_capability(plugin, MEMORY_WRITEBACK, "writeback")
    return plugin


def load_pattern_plugin(ref: PatternRef) -> Any:
    plugin = _load_plugin("pattern", ref)
    _validate_required_capabilities(plugin, {PATTERN_EXECUTE}, "pattern plugin")
    _validate_method_for_capability(plugin, PATTERN_EXECUTE, "execute")
    _validate_method_for_capability(plugin, PATTERN_REACT, "react")
    return plugin


def load_tool_plugin(ref: ToolRef) -> Any:
    plugin = _load_plugin("tool", ref)
    _validate_required_capabilities(plugin, {TOOL_INVOKE}, f"tool plugin '{ref.id}'")
    _validate_method_for_capability(plugin, TOOL_INVOKE, "invoke")
    return plugin


def load_skill_plugin(ref: SkillRef | None) -> Any | None:
    if ref is None:
        return None

    plugin = _load_plugin("skill", ref)
    skill_capabilities = _capability_set(plugin)
    supported_caps = {
        SKILL_SYSTEM_PROMPT,
        SKILL_TOOLS,
        SKILL_METADATA,
        SKILL_CONTEXT_AUGMENT,
        SKILL_TOOL_FILTER,
        SKILL_PRE_RUN,
        SKILL_POST_RUN,
    }
    if not (skill_capabilities & supported_caps):
        raise CapabilityError(
            "skill plugin must declare at least one of "
            f"{sorted(supported_caps)}"
        )
    _validate_method_for_capability(plugin, SKILL_SYSTEM_PROMPT, "get_system_prompt")
    _validate_method_for_capability(plugin, SKILL_TOOLS, "get_tools")
    _validate_method_for_capability(plugin, SKILL_METADATA, "get_metadata")
    _validate_method_for_capability(plugin, SKILL_CONTEXT_AUGMENT, "augment_context")
    _validate_method_for_capability(plugin, SKILL_TOOL_FILTER, "filter_tools")
    _validate_method_for_capability(plugin, SKILL_PRE_RUN, "before_run")
    _validate_method_for_capability(plugin, SKILL_POST_RUN, "after_run")
    return plugin


def load_tool_executor_plugin(ref: ToolExecutorRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin("tool_executor", ref)
    if not callable(getattr(plugin, "execute", None)):
        raise CapabilityError(
            f"tool executor '{type(plugin).__name__}' must implement 'execute'"
        )
    if not callable(getattr(plugin, "execute_stream", None)):
        raise CapabilityError(
            f"tool executor '{type(plugin).__name__}' must implement 'execute_stream'"
        )
    return plugin


def load_execution_policy_plugin(ref: ExecutionPolicyRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin("execution_policy", ref)
    if not callable(getattr(plugin, "evaluate", None)):
        raise CapabilityError(
            f"execution policy '{type(plugin).__name__}' must implement 'evaluate'"
        )
    return plugin


def load_context_assembler_plugin(ref: ContextAssemblerRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin("context_assembler", ref)
    if not callable(getattr(plugin, "assemble", None)):
        raise CapabilityError(
            f"context assembler '{type(plugin).__name__}' must implement 'assemble'"
        )
    if not callable(getattr(plugin, "finalize", None)):
        raise CapabilityError(
            f"context assembler '{type(plugin).__name__}' must implement 'finalize'"
        )
    return plugin


def _normalize_skill_tool_ref(item: Any, index: int) -> ToolRef:
    if isinstance(item, str):
        tool_id = item.strip()
        if not tool_id:
            raise PluginLoadError(f"skill tools[{index}] must not be empty")
        return ToolRef(id=tool_id, type=tool_id)
    if isinstance(item, ToolRef):
        return item
    if isinstance(item, dict):
        return ToolRef.from_dict(item, index)
    raise PluginLoadError(
        f"skill tools[{index}] must be a string, object, or ToolRef, got {type(item).__name__}"
    )


def _load_skill_tools(skill: Any) -> dict[str, Any]:
    raw_tools = skill.get_tools()
    if raw_tools is None:
        return {}
    if not isinstance(raw_tools, list):
        raise PluginLoadError("skill.get_tools() must return a list")

    tools: dict[str, Any] = {}
    for index, item in enumerate(raw_tools):
        tool_ref = _normalize_skill_tool_ref(item, index)
        if not tool_ref.enabled:
            continue
        tools[tool_ref.id] = load_tool_plugin(tool_ref)
    return tools


def load_agent_plugins(agent: AgentDefinition) -> LoadedAgentPlugins:
    memory = load_memory_plugin(agent.memory)
    pattern = load_pattern_plugin(agent.pattern)
    skill = load_skill_plugin(agent.skill)
    tool_executor = load_tool_executor_plugin(agent.tool_executor)
    execution_policy = load_execution_policy_plugin(agent.execution_policy)
    context_assembler = load_context_assembler_plugin(agent.context_assembler)

    tools: dict[str, Any] = {}
    if skill is not None and SKILL_TOOLS in _capability_set(skill):
        tools.update(_load_skill_tools(skill))
    for tool_ref in agent.tools:
        if not tool_ref.enabled:
            continue
        tools[tool_ref.id] = load_tool_plugin(tool_ref)

    return LoadedAgentPlugins(
        memory=memory,
        pattern=pattern,
        skill=skill,
        tool_executor=tool_executor,
        execution_policy=execution_policy,
        context_assembler=context_assembler,
        tools=tools,
    )


def load_runtime_plugin(ref: RuntimeRef) -> Any:
    """Load a runtime plugin."""
    plugin = _load_plugin("runtime", ref)
    _validate_required_capabilities(plugin, {RUNTIME_RUN}, "runtime plugin")
    _validate_method_for_capability(plugin, RUNTIME_RUN, "run")
    return plugin


def load_session_plugin(ref: SessionRef) -> Any:
    """Load a session manager plugin."""
    plugin = _load_plugin("session", ref)
    _validate_required_capabilities(plugin, {SESSION_MANAGE}, "session plugin")
    _validate_method_for_capability(plugin, SESSION_MANAGE, "session")
    return plugin


def load_events_plugin(ref: EventBusRef) -> Any:
    """Load an event bus plugin."""
    plugin = _load_plugin("events", ref)
    _validate_required_capabilities(plugin, {EVENT_EMIT}, "event bus plugin")
    _validate_method_for_capability(plugin, EVENT_EMIT, "emit")
    _validate_method_for_capability(plugin, EVENT_SUBSCRIBE, "subscribe")
    return plugin


def load_runtime_components(
    runtime_ref: RuntimeRef | None,
    session_ref: SessionRef | None,
    events_ref: EventBusRef | None,
) -> LoadedRuntimeComponents:
    """Load all runtime components from config references.

    Uses defaults if refs are None.
    Handles dependency injection between components.
    """
    from openagents.config.schema import EventBusRef as DefaultEventBusRef
    from openagents.config.schema import RuntimeRef as DefaultRuntimeRef
    from openagents.config.schema import SessionRef as DefaultSessionRef

    # Load events first (no dependencies)
    events = load_events_plugin(events_ref or DefaultEventBusRef(type="async"))

    # Load session (no dependencies)
    session = load_session_plugin(session_ref or DefaultSessionRef(type="in_memory"))

    # Load runtime with injected dependencies
    runtime = load_runtime_plugin(runtime_ref or DefaultRuntimeRef(type="default"))

    # Inject dependencies into runtime if it supports it
    if hasattr(runtime, "_event_bus"):
        runtime._event_bus = events
    if hasattr(runtime, "_session_manager"):
        runtime._session_manager = session

    return LoadedRuntimeComponents(runtime=runtime, session=session, events=events)
