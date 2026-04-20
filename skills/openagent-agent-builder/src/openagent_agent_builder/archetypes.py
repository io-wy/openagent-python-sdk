"""Archetype defaults for agent-builder synthesis."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _tool(tool_id: str, tool_type: str | None = None) -> dict[str, Any]:
    return {"id": tool_id, "type": tool_type or tool_id}


_ARCHETYPES: dict[str, dict[str, Any]] = {
    "planner": {
        "agent_name": "Task Planner",
        "purpose_suffix": "Plan the work and produce structured steps.",
        "prompt_summary": "Planning-focused agent that breaks tasks into explicit steps.",
        "design_rationale": "Uses plan_execute to prefer structured planning over direct execution.",
        "memory": {"type": "window_buffer", "on_error": "continue", "config": {"window_size": 8}},
        "pattern": {"type": "plan_execute", "config": {"max_steps": 6}},
        "llm": {"provider": "mock", "temperature": 0.0},
        "tools": [_tool("search", "builtin_search"), _tool("read_file"), _tool("list_files")],
        "runtime": {"max_steps": 6, "step_timeout_ms": 30000, "session_queue_size": 1000, "event_queue_size": 2000},
        "handoff_contract": {"expected_input": "task brief", "expected_output": "plan", "artifact_format": "markdown"},
        "integration_hints": {"preferred_position": "upstream", "notes": ["Use before coder/reviewer agents."]},
    },
    "coder": {
        "agent_name": "Task Coder",
        "purpose_suffix": "Modify files and implement scoped changes.",
        "prompt_summary": "Execution-focused coding agent with filesystem-safe tooling.",
        "design_rationale": "Uses react plus safe executor for iterative file-oriented work.",
        "memory": {"type": "window_buffer", "on_error": "continue", "config": {"window_size": 10}},
        "pattern": {"type": "react", "config": {"max_steps": 8}},
        "llm": {"provider": "mock", "temperature": 0.0},
        "tool_executor": {"type": "safe", "config": {"default_timeout_ms": 30000}},
        "tools": [_tool("read_file"), _tool("write_file"), _tool("list_files"), _tool("grep_files"), _tool("ripgrep")],
        "runtime": {"max_steps": 8, "step_timeout_ms": 30000, "session_queue_size": 1000, "event_queue_size": 2000},
        "handoff_contract": {
            "expected_input": "task packet",
            "expected_output": "patch or implementation note",
            "artifact_format": "markdown",
        },
        "integration_hints": {"preferred_position": "middle", "notes": ["Pair with reviewer for validation."]},
    },
    "reviewer": {
        "agent_name": "Patch Reviewer",
        "purpose_suffix": "Review a patch and return structured findings.",
        "prompt_summary": "Review-focused agent biased toward read-only inspection and structured findings.",
        "design_rationale": "Uses react with read-oriented tools to inspect code and summarize issues.",
        "memory": {"type": "window_buffer", "on_error": "continue", "config": {"window_size": 12}},
        "pattern": {"type": "react", "config": {"max_steps": 6}},
        "llm": {"provider": "mock", "temperature": 0.0},
        "tool_executor": {"type": "safe", "config": {"default_timeout_ms": 30000}},
        "tools": [
            _tool("read_file"),
            _tool("list_files"),
            _tool("grep_files"),
            _tool("ripgrep"),
            _tool("search", "builtin_search"),
        ],
        "runtime": {"max_steps": 6, "step_timeout_ms": 30000, "session_queue_size": 1000, "event_queue_size": 2000},
        "handoff_contract": {
            "expected_input": "patch or diff",
            "expected_output": "findings",
            "artifact_format": "markdown",
        },
        "integration_hints": {
            "preferred_position": "downstream",
            "notes": ["Feed reviewer output back to the main agent or coder."],
        },
    },
    "researcher": {
        "agent_name": "Research Agent",
        "purpose_suffix": "Collect evidence and synthesize findings.",
        "prompt_summary": "Research-focused agent that can iterate and reflect before returning findings.",
        "design_rationale": "Uses reflexion to support iterative evidence gathering and reconsideration.",
        "memory": {"type": "window_buffer", "on_error": "continue", "config": {"window_size": 12}},
        "pattern": {"type": "reflexion", "config": {"max_steps": 8, "max_retries": 2}},
        "llm": {"provider": "mock", "temperature": 0.0},
        "tool_executor": {"type": "safe", "config": {"default_timeout_ms": 30000}},
        "tools": [_tool("search", "builtin_search"), _tool("http_request"), _tool("url_parse"), _tool("query_param")],
        "runtime": {"max_steps": 8, "step_timeout_ms": 30000, "session_queue_size": 1000, "event_queue_size": 2000},
        "handoff_contract": {
            "expected_input": "research brief",
            "expected_output": "evidence summary",
            "artifact_format": "markdown",
        },
        "integration_hints": {
            "preferred_position": "upstream",
            "notes": ["Use to prepare context for planner or reviewer agents."],
        },
    },
}


def list_archetypes() -> list[str]:
    return sorted(_ARCHETYPES)


def resolve_archetype(name: str) -> dict[str, Any]:
    try:
        return deepcopy(_ARCHETYPES[name])
    except KeyError as exc:
        raise ValueError(f"Unknown archetype: {name}") from exc
