"""Plugin loader and capability checks."""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass
from typing import Any

from openagents.config.schema import (
    AgentDefinition,
    ContextAssemblerRef,
    ExecutionPolicyRef,
    EventBusRef,
    FollowupResolverRef,
    MemoryRef,
    PatternRef,
    PluginRef,
    ResponseRepairPolicyRef,
    RuntimeRef,
    SkillsRef,
    SessionRef,
    ToolExecutorRef,
    ToolRef,
)
from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    PATTERN_EXECUTE,
    PATTERN_REACT,
    TOOL_INVOKE,
    normalize_capabilities,
)
from openagents.interfaces.runtime import RUNTIME_RUN
from openagents.interfaces.session import SESSION_MANAGE
from openagents.interfaces.skills import SkillsPlugin
from openagents.interfaces.events import EVENT_EMIT, EVENT_SUBSCRIBE
from openagents.errors.exceptions import CapabilityError, PluginLoadError
from openagents.errors.suggestions import near_match
from openagents.plugins.registry import get_builtin_plugin_class, list_builtin_plugins


@dataclass
class LoadedAgentPlugins:
    memory: Any
    pattern: Any
    tool_executor: Any | None
    execution_policy: Any | None
    context_assembler: Any | None
    followup_resolver: Any | None
    response_repair_policy: Any | None
    tools: dict[str, Any]


@dataclass
class LoadedRuntimeComponents:
    runtime: Any
    session: Any
    events: Any
    skills: Any


def _import_symbol(path: str) -> Any:
    if "." not in path:
        raise PluginLoadError(
            f"Invalid impl path: '{path}'",
            hint="impl path must be 'module.path:Symbol' or 'module.path.Symbol'",
        )
    module_name, attr_name = path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - defensive
        raise PluginLoadError(
            f"Failed to import module '{module_name}'",
            hint=f"check the spelling and that '{module_name}' is importable on this Python path",
        ) from exc
    try:
        return getattr(module, attr_name)
    except AttributeError as exc:
        raise PluginLoadError(
            f"Module '{module_name}' has no symbol '{attr_name}'",
            hint=f"check the spelling of '{attr_name}' in module '{module_name}'",
        ) from exc


def _instantiate(factory: Any, config: dict[str, Any]) -> Any:
    if not callable(factory):
        return factory
    try:
        return factory(config=config)
    except TypeError as exc:
        raise PluginLoadError(
            f"Could not instantiate plugin from {factory!r}: {exc}"
        ) from exc


def _validate_class_methods(factory: Any, *, required_methods: tuple[str, ...], where: str) -> None:
    if not isinstance(factory, type):
        return
    for method_name in required_methods:
        if not callable(getattr(factory, method_name, None)):
            raise CapabilityError(
                f"{where} '{factory.__name__}' must implement '{method_name}'"
            )


def _load_plugin_impl(kind: str, ref: PluginRef, *, required_methods: tuple[str, ...] = ()) -> Any:
    # impl takes priority if provided
    if ref.impl:
        symbol = _import_symbol(ref.impl)
        _validate_class_methods(symbol, required_methods=required_methods, where=f"{kind} plugin")
        return _instantiate(symbol, ref.config)
    # Fall back to type (builtin or decorator-registered)
    if ref.type:
        # 0.3.0 deprecated-rename guard: the old "summarizing" context_assembler
        # never actually summarized, just truncated. Direct users explicitly to
        # the new name rather than a generic "unknown plugin" error.
        if kind == "context_assembler" and ref.type == "summarizing":
            raise PluginLoadError(
                "context_assembler type 'summarizing' was renamed to 'truncating' in 0.3.0 "
                "because the old implementation only truncated without summarizing. "
                "Rename to 'truncating', or set impl= to your own LLM-based summarizer."
            )
        plugin_cls = get_builtin_plugin_class(kind, ref.type)
        if plugin_cls is None:
            available = list_builtin_plugins(kind)
            guess = near_match(ref.type, available)
            if guess:
                hint_text = (
                    f"Did you mean '{guess}'? Available {kind} plugins: {available}"
                )
            else:
                hint_text = f"Available {kind} plugins: {available}"
            raise PluginLoadError(
                f"Unknown {kind} plugin type: '{ref.type}'",
                hint=hint_text,
            )
        _validate_class_methods(plugin_cls, required_methods=required_methods, where=f"{kind} plugin")
        return _instantiate(plugin_cls, ref.config)
    raise PluginLoadError(
        f"{kind} plugin must set one of 'type' or 'impl'",
        hint="add 'type: \"<name>\"' for a builtin or 'impl: \"module.path:Class\"' for a custom plugin",
    )


def load_plugin(
    kind: str,
    ref: PluginRef,
    *,
    required_methods: tuple[str, ...] = (),
) -> Any:
    """Load a child plugin from a PluginRef.

    Public entry point used by combinator builtins that compose other
    plugins (memory.chain, tool_executor.retry, execution_policy.composite,
    events.file_logging) and by external custom combinators.
    """
    return _load_plugin_impl(kind, ref, required_methods=required_methods)


def _load_plugin(kind: str, ref: PluginRef, *, required_methods: tuple[str, ...] = ()) -> Any:
    """Deprecated alias for :func:`load_plugin`.

    Kept for one release so external combinator plugins keep working.
    Emits a DeprecationWarning at call time.
    """
    warnings.warn(
        "openagents.plugins.loader._load_plugin is deprecated; "
        "use openagents.plugins.loader.load_plugin",
        DeprecationWarning,
        stacklevel=2,
    )
    return _load_plugin_impl(kind, ref, required_methods=required_methods)


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
    plugin = _load_plugin_impl("memory", ref)
    _validate_method_for_capability(plugin, MEMORY_INJECT, "inject")
    _validate_method_for_capability(plugin, MEMORY_WRITEBACK, "writeback")
    return plugin


def load_pattern_plugin(ref: PatternRef) -> Any:
    plugin = _load_plugin_impl("pattern", ref, required_methods=("execute", "react"))
    _validate_required_capabilities(plugin, {PATTERN_EXECUTE}, "pattern plugin")
    _validate_method_for_capability(plugin, PATTERN_EXECUTE, "execute")
    _validate_method_for_capability(plugin, PATTERN_REACT, "react")
    return plugin


def load_tool_plugin(ref: ToolRef) -> Any:
    plugin = _load_plugin_impl("tool", ref, required_methods=("invoke",))
    _validate_required_capabilities(plugin, {TOOL_INVOKE}, f"tool plugin '{ref.id}'")
    _validate_method_for_capability(plugin, TOOL_INVOKE, "invoke")
    return plugin


def load_tool_executor_plugin(ref: ToolExecutorRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin_impl("tool_executor", ref, required_methods=("execute", "execute_stream"))
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
    plugin = _load_plugin_impl("execution_policy", ref, required_methods=("evaluate",))
    if not callable(getattr(plugin, "evaluate", None)):
        raise CapabilityError(
            f"execution policy '{type(plugin).__name__}' must implement 'evaluate'"
        )
    return plugin


def load_context_assembler_plugin(ref: ContextAssemblerRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin_impl("context_assembler", ref, required_methods=("assemble", "finalize"))
    if not callable(getattr(plugin, "assemble", None)):
        raise CapabilityError(
            f"context assembler '{type(plugin).__name__}' must implement 'assemble'"
        )
    if not callable(getattr(plugin, "finalize", None)):
        raise CapabilityError(
            f"context assembler '{type(plugin).__name__}' must implement 'finalize'"
        )
    return plugin


def load_followup_resolver_plugin(ref: FollowupResolverRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin_impl("followup_resolver", ref, required_methods=("resolve",))
    if not callable(getattr(plugin, "resolve", None)):
        raise CapabilityError(
            f"follow-up resolver '{type(plugin).__name__}' must implement 'resolve'"
        )
    return plugin


def load_response_repair_policy_plugin(ref: ResponseRepairPolicyRef | None) -> Any | None:
    if ref is None:
        return None
    plugin = _load_plugin_impl("response_repair_policy", ref, required_methods=("repair_empty_response",))
    if not callable(getattr(plugin, "repair_empty_response", None)):
        raise CapabilityError(
            f"response repair policy '{type(plugin).__name__}' must implement 'repair_empty_response'"
        )
    return plugin

def load_agent_plugins(agent: AgentDefinition) -> LoadedAgentPlugins:
    memory = load_memory_plugin(agent.memory)
    pattern = load_pattern_plugin(agent.pattern)
    tool_executor = load_tool_executor_plugin(agent.tool_executor)
    execution_policy = load_execution_policy_plugin(agent.execution_policy)
    context_assembler = load_context_assembler_plugin(agent.context_assembler)
    followup_resolver = load_followup_resolver_plugin(agent.followup_resolver)
    response_repair_policy = load_response_repair_policy_plugin(agent.response_repair_policy)

    tools: dict[str, Any] = {}
    for tool_ref in agent.tools:
        if not tool_ref.enabled:
            continue
        tools[tool_ref.id] = load_tool_plugin(tool_ref)

    return LoadedAgentPlugins(
        memory=memory,
        pattern=pattern,
        tool_executor=tool_executor,
        execution_policy=execution_policy,
        context_assembler=context_assembler,
        followup_resolver=followup_resolver,
        response_repair_policy=response_repair_policy,
        tools=tools,
    )


def load_runtime_plugin(ref: RuntimeRef) -> Any:
    """Load a runtime plugin."""
    plugin = _load_plugin_impl("runtime", ref, required_methods=("run",))
    _validate_required_capabilities(plugin, {RUNTIME_RUN}, "runtime plugin")
    _validate_method_for_capability(plugin, RUNTIME_RUN, "run")
    return plugin


def load_session_plugin(ref: SessionRef) -> Any:
    """Load a session manager plugin."""
    plugin = _load_plugin_impl("session", ref, required_methods=("session",))
    _validate_required_capabilities(plugin, {SESSION_MANAGE}, "session plugin")
    _validate_method_for_capability(plugin, SESSION_MANAGE, "session")
    return plugin


def load_events_plugin(ref: EventBusRef) -> Any:
    """Load an event bus plugin."""
    plugin = _load_plugin_impl("events", ref, required_methods=("emit", "subscribe"))
    _validate_required_capabilities(plugin, {EVENT_EMIT}, "event bus plugin")
    _validate_method_for_capability(plugin, EVENT_EMIT, "emit")
    _validate_method_for_capability(plugin, EVENT_SUBSCRIBE, "subscribe")
    return plugin


def load_skills_plugin(ref: SkillsRef | None) -> SkillsPlugin:
    from openagents.config.schema import SkillsRef as DefaultSkillsRef

    actual = ref or DefaultSkillsRef(type="local")
    plugin = _load_plugin_impl("skills", actual, required_methods=("prepare_session", "load_references", "run_skill"))
    for method_name in ("prepare_session", "load_references", "run_skill"):
        if not callable(getattr(plugin, method_name, None)):
            raise CapabilityError(
                f"skills component '{type(plugin).__name__}' must implement '{method_name}'"
            )
    return plugin


def load_runtime_components(
    runtime_ref: RuntimeRef | None,
    session_ref: SessionRef | None,
    events_ref: EventBusRef | None,
    skills_ref: SkillsRef | None,
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

    # Load host-level skills manager and inject session dependency when available
    skills = load_skills_plugin(skills_ref)
    if hasattr(skills, "_session_manager"):
        skills._session_manager = session

    # Load runtime with injected dependencies
    runtime = load_runtime_plugin(runtime_ref or DefaultRuntimeRef(type="default"))

    # Inject dependencies into runtime if it supports it
    if hasattr(runtime, "_event_bus"):
        runtime._event_bus = events
    if hasattr(runtime, "_session_manager"):
        runtime._session_manager = session

    return LoadedRuntimeComponents(runtime=runtime, session=session, events=events, skills=skills)
