"""Synchronous runtime helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openagents.interfaces.runtime import RunRequest, RunResult
from openagents.runtime.runtime import Runtime


def run_agent(
    config_path: str | Path,
    *,
    agent_id: str,
    session_id: str = "default",
    input_text: str,
    deps: Any = None,
) -> Any:
    """Synchronous agent execution.

    Convenience function for non-async contexts.
    Creates a Runtime from config and runs the agent synchronously.

    Args:
        config_path: Path to agent configuration JSON file
        agent_id: Agent ID to execute
        session_id: Session ID (default: "default")
        input_text: Input text for the agent

    Returns:
        Agent execution result

    Example:
        >>> result = run_agent("agent.json", agent_id="assistant", input_text="hello")
    """
    runtime = Runtime.from_config(config_path)
    return runtime.run_sync(agent_id=agent_id, session_id=session_id, input_text=input_text, deps=deps)


def run_agent_with_config(
    config: Any,
    *,
    agent_id: str,
    session_id: str = "default",
    input_text: str,
    deps: Any = None,
) -> Any:
    """Synchronous agent execution with pre-loaded config.

    Args:
        config: AppConfig object (from load_config())
        agent_id: Agent ID to execute
        session_id: Session ID (default: "default")
        input_text: Input text for the agent

    Returns:
        Agent execution result
    """
    runtime = Runtime(config, _skip_plugin_load=False)
    return runtime.run_sync(agent_id=agent_id, session_id=session_id, input_text=input_text, deps=deps)


def run_agent_detailed(
    config_path: str | Path,
    *,
    agent_id: str,
    session_id: str = "default",
    input_text: str,
    deps: Any = None,
) -> RunResult:
    """Synchronous agent execution that returns the full RunResult."""
    runtime = Runtime.from_config(config_path)
    return asyncio.run(
        runtime.run_detailed(
            request=RunRequest(
                agent_id=agent_id,
                session_id=session_id,
                input_text=input_text,
                deps=deps,
            )
        )
    )


def run_agent_detailed_with_config(
    config: Any,
    *,
    agent_id: str,
    session_id: str = "default",
    input_text: str,
    deps: Any = None,
) -> RunResult:
    """Synchronous detailed run with a pre-loaded AppConfig."""
    runtime = Runtime(config, _skip_plugin_load=False)
    return asyncio.run(
        runtime.run_detailed(
            request=RunRequest(
                agent_id=agent_id,
                session_id=session_id,
                input_text=input_text,
                deps=deps,
            )
        )
    )


def run_agent_with_dict(
    payload: dict[str, Any],
    *,
    agent_id: str,
    session_id: str = "default",
    input_text: str,
    deps: Any = None,
) -> Any:
    """Synchronous agent execution directly from a Python config dict."""
    runtime = Runtime.from_dict(payload)
    return runtime.run_sync(agent_id=agent_id, session_id=session_id, input_text=input_text, deps=deps)


def stream_agent_with_dict(
    payload: dict[str, Any],
    *,
    request: RunRequest,
):
    """Synchronous streaming from a pre-loaded config dict.

    Yields :class:`RunStreamChunk` objects. Uses an asyncio event loop
    under the hood; safe to call from non-async contexts but not from
    inside a running loop.
    """
    from openagents.interfaces.runtime import RunStreamChunk  # noqa: F401

    runtime = Runtime.from_dict(payload)

    async def _collect() -> list:
        chunks = []
        async for chunk in runtime.run_stream(request=request):
            chunks.append(chunk)
        return chunks

    for chunk in asyncio.run(_collect()):
        yield chunk


def stream_agent_with_config(
    config_path: str | Path,
    *,
    request: RunRequest,
):
    """Synchronous streaming from a config file path."""
    import json

    with open(config_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    yield from stream_agent_with_dict(payload, request=request)
