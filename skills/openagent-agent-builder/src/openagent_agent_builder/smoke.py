"""Smoke-run helpers for generated single-agent specs."""

from __future__ import annotations

from typing import Any


async def smoke_run_agent_spec(spec_bundle: dict[str, Any], smoke_input: str = "hello") -> dict[str, Any]:
    from openagents.interfaces.runtime import RUN_STOP_FAILED, RunRequest
    from openagents.runtime.runtime import Runtime

    runtime: Runtime | None = None
    request_template = spec_bundle.get("run_request_template", {})
    agent_id = request_template.get("agent_id", spec_bundle.get("agent_key", "assistant"))
    input_text = smoke_input or spec_bundle.get("purpose", "hello")

    try:
        runtime = Runtime.from_dict(spec_bundle["sdk_config"])
        result = await runtime.run_detailed(
            request=RunRequest(
                agent_id=agent_id,
                session_id="openagent-skill-smoke",
                input_text=input_text,
                context_hints=dict(request_template.get("context_hints", {})),
                metadata=dict(request_template.get("metadata", {})),
            )
        )
    except Exception as exc:
        return {
            "status": "failed",
            "agent_id": agent_id,
            "input": input_text,
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    finally:
        if runtime is not None:
            await runtime.close()

    if result.error_details is not None or result.stop_reason == RUN_STOP_FAILED:
        error_msg = result.error_details.message if result.error_details is not None else "run failed"
        return {
            "status": "failed",
            "agent_id": agent_id,
            "input": input_text,
            "error": error_msg,
            "stop_reason": result.stop_reason,
        }

    return {
        "status": "passed",
        "agent_id": agent_id,
        "input": input_text,
        "result": result.final_output,
        "stop_reason": result.stop_reason,
    }
