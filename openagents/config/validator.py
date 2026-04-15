"""Validation rules for agent config."""

from __future__ import annotations

from .schema import AppConfig, LLMOptions, PluginRef, RuntimeOptions
from ..errors.exceptions import ConfigError

_MEMORY_ON_ERROR_VALUES = {"continue", "fail"}
_LLM_PROVIDER_VALUES = {"anthropic", "mock", "openai_compatible"}


def _is_non_empty_str(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_plugin_selector(plugin: PluginRef, where: str) -> None:
    has_type = _is_non_empty_str(plugin.type)
    has_impl = _is_non_empty_str(plugin.impl)
    # Allow both type and impl (impl takes priority), but require at least one
    if not has_type and not has_impl:
        raise ConfigError(f"'{where}' must set at least one of 'type' or 'impl'")


def _validate_positive_int(value: int, where: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"'{where}' must be a positive integer")


def _validate_runtime(runtime: RuntimeOptions, where: str) -> None:
    _validate_positive_int(runtime.max_steps, f"{where}.max_steps")
    _validate_positive_int(runtime.step_timeout_ms, f"{where}.step_timeout_ms")
    _validate_positive_int(runtime.session_queue_size, f"{where}.session_queue_size")
    _validate_positive_int(runtime.event_queue_size, f"{where}.event_queue_size")


def _validate_llm(llm: LLMOptions | None, where: str) -> None:
    if llm is None:
        return
    if llm.provider not in _LLM_PROVIDER_VALUES:
        raise ConfigError(
            f"'{where}.provider' must be one of {sorted(_LLM_PROVIDER_VALUES)}"
        )
    if llm.provider == "openai_compatible" and not _is_non_empty_str(llm.api_base):
        raise ConfigError(f"'{where}.api_base' is required for provider 'openai_compatible'")
    if llm.max_tokens is not None:
        _validate_positive_int(llm.max_tokens, f"{where}.max_tokens")
    _validate_positive_int(llm.timeout_ms, f"{where}.timeout_ms")


def validate_config(config: AppConfig) -> None:
    if not config.agents:
        raise ConfigError("'agents' must contain at least one item")

    seen_agent_ids: set[str] = set()
    for agent in config.agents:
        if agent.id in seen_agent_ids:
            raise ConfigError(f"Duplicate agent id: '{agent.id}'")
        seen_agent_ids.add(agent.id)

        _validate_plugin_selector(agent.memory, f"agents['{agent.id}'].memory")
        _validate_plugin_selector(agent.pattern, f"agents['{agent.id}'].pattern")
        if agent.tool_executor is not None:
            _validate_plugin_selector(agent.tool_executor, f"agents['{agent.id}'].tool_executor")
        if agent.execution_policy is not None:
            _validate_plugin_selector(agent.execution_policy, f"agents['{agent.id}'].execution_policy")
        if agent.context_assembler is not None:
            _validate_plugin_selector(agent.context_assembler, f"agents['{agent.id}'].context_assembler")
        if agent.followup_resolver is not None:
            _validate_plugin_selector(agent.followup_resolver, f"agents['{agent.id}'].followup_resolver")
        if agent.response_repair_policy is not None:
            _validate_plugin_selector(agent.response_repair_policy, f"agents['{agent.id}'].response_repair_policy")
        _validate_llm(agent.llm, f"agents['{agent.id}'].llm")

        if agent.memory.on_error not in _MEMORY_ON_ERROR_VALUES:
            raise ConfigError(
                f"agents['{agent.id}'].memory.on_error must be one of "
                f"{sorted(_MEMORY_ON_ERROR_VALUES)}"
            )

        seen_tool_ids: set[str] = set()
        for tool in agent.tools:
            _validate_plugin_selector(tool, f"agents['{agent.id}'].tools['{tool.id}']")
            if tool.id in seen_tool_ids:
                raise ConfigError(f"Duplicate tool id in agent '{agent.id}': '{tool.id}'")
            seen_tool_ids.add(tool.id)

        _validate_runtime(agent.runtime, f"agents['{agent.id}'].runtime")
