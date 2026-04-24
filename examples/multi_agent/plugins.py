"""Custom tools that invoke the agent_router seam.

What:
    Two thin wrappers around ``ctx.agent_router``: ``DelegateToSpecialistTool``
    awaits a sub-agent and returns its output to the parent; ``TransferToBillingTool``
    hands control over permanently by raising ``HandoffSignal``.

Usage:
    Register via ``impl`` in the agent config, e.g.
    ``{"id": "delegate_to_specialist", "impl":
    "examples.multi_agent.plugins.DelegateToSpecialistTool"}``.

Depends on:
    - ``RunContext.agent_router`` being non-None (config ``multi_agent.enabled: true``).
"""

from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolPlugin


def _ensure_router(context: Any) -> Any:
    router = getattr(context, "agent_router", None)
    if router is None:
        raise RuntimeError("agent_router is not configured. Set 'multi_agent.enabled: true' in AppConfig.")
    return router


class DelegateToSpecialistTool(ToolPlugin):
    """Delegate a research / lookup task to the specialist agent and return its output.

    What:
        Awaits ``ctx.agent_router.delegate("specialist", query, ctx)`` so the
        calling agent can combine the specialist's answer with its own reasoning.

    Usage:
        ``{"id": "delegate_to_specialist", "impl":
        "examples.multi_agent.plugins.DelegateToSpecialistTool"}``.

    Depends on:
        - An agent with ``id="specialist"`` defined in the same AppConfig.
        - ``multi_agent.enabled: true``.
    """

    name = "delegate_to_specialist"
    description = (
        "Delegate a research or lookup subtask to the specialist agent and receive its answer. "
        "Use this when the user asks a factual question you want a specialist to answer."
    )
    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        router = _ensure_router(context)
        query = str((params or {}).get("query", "")).strip() or "(empty query)"
        result = await router.delegate(
            "specialist",
            query,
            context,
            session_isolation="isolated",
        )
        return {
            "delegated_to": "specialist",
            "child_run_id": result.run_id,
            "output": result.final_output,
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Question for the specialist."}},
            "required": ["query"],
        }


class TransferToBillingTool(ToolPlugin):
    """Transfer control to the billing agent; the triage run ends with billing's final output.

    What:
        Calls ``ctx.agent_router.transfer("billing_agent", query, ctx)``, which raises
        ``HandoffSignal``. ``DefaultRuntime`` catches the signal and returns the child's
        ``final_output`` as the parent run's result, along with ``metadata['handoff_from']``.

    Usage:
        ``{"id": "transfer_to_billing", "impl":
        "examples.multi_agent.plugins.TransferToBillingTool"}``.

    Depends on:
        - An agent with ``id="billing_agent"`` defined in the same AppConfig.
        - ``multi_agent.enabled: true``.
    """

    name = "transfer_to_billing"
    description = (
        "Hand control to the billing agent for refunds, invoices, or payment disputes. "
        "Use this when the user's request is clearly a billing issue."
    )
    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        router = _ensure_router(context)
        query = str((params or {}).get("query", "")).strip() or "(empty query)"
        # transfer() raises HandoffSignal; control never returns here.
        await router.transfer(
            "billing_agent",
            query,
            context,
            session_isolation="isolated",
        )
        # Unreachable; kept for type-checkers.
        return None  # pragma: no cover

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Billing-related user request to hand off to billing_agent.",
                }
            },
            "required": ["query"],
        }
