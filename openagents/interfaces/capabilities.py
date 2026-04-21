"""Capability constants and helpers."""

from __future__ import annotations

from typing import Any, Iterable

MEMORY_INJECT = "memory.inject"
MEMORY_WRITEBACK = "memory.writeback"
MEMORY_RETRIEVE = "memory.retrieve"
PATTERN_REACT = "pattern.react"
PATTERN_EXECUTE = "pattern.execute"
TOOL_INVOKE = "tool.invoke"
SKILL_SYSTEM_PROMPT = "skill.system_prompt"
SKILL_TOOLS = "skill.tools"
SKILL_METADATA = "skill.metadata"
SKILL_CONTEXT_AUGMENT = "skill.context_augment"
SKILL_TOOL_FILTER = "skill.tool_filter"
SKILL_PRE_RUN = "skill.pre_run"
SKILL_POST_RUN = "skill.post_run"
DIAG_METRICS = "diagnostics.metrics"
DIAG_ERROR = "diagnostics.error"
DIAG_EXPORT = "diagnostics.export"

KNOWN_CAPABILITIES = {
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    MEMORY_RETRIEVE,
    PATTERN_REACT,
    PATTERN_EXECUTE,
    TOOL_INVOKE,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOLS,
    SKILL_METADATA,
    SKILL_CONTEXT_AUGMENT,
    SKILL_TOOL_FILTER,
    SKILL_PRE_RUN,
    SKILL_POST_RUN,
    DIAG_METRICS,
    DIAG_ERROR,
    DIAG_EXPORT,
}


def normalize_capabilities(values: Iterable[str] | None) -> set[str]:
    if values is None:
        return set()
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if item:
            normalized.add(item)
    return normalized


def supports(plugin: Any, capability: str) -> bool:
    """Check if a plugin supports a specific capability.

    Args:
        plugin: Plugin instance to check
        capability: Capability string to look for

    Returns:
        True if plugin has the capability
    """
    capabilities = normalize_capabilities(getattr(plugin, "capabilities", set()))
    return capability in capabilities
