"""Composite policy helper (AND/OR combinator)."""

from __future__ import annotations

from typing import Any, Literal

from openagents.interfaces.tool import PolicyDecision, ToolExecutionRequest


class CompositePolicy:
    """Combine multiple policy helpers with AND (``all``) or OR (``any``) semantics.

    Accepts a list of already-instantiated policy helpers (e.g. FilesystemExecutionPolicy,
    NetworkAllowlistExecutionPolicy) rather than config dicts. Each child must expose
    ``async evaluate_policy(request) -> PolicyDecision``.

    Usage:
        children = [
            FilesystemExecutionPolicy(config={"read_roots": ["./data"]}),
            NetworkAllowlistExecutionPolicy(config={"allow_hosts": ["api.example.com"]}),
        ]
        composite = CompositePolicy(children=children, mode="all")
        decision = await composite.evaluate_policy(request)
    """

    def __init__(
        self,
        children: list[Any],
        mode: Literal["all", "any"] = "all",
    ):
        self._children = children
        self._mode = mode

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        child_metadata: list[dict[str, Any]] = []
        if not self._children:
            return PolicyDecision(
                allowed=True,
                metadata={"policy": "composite", "children": [], "decided_by": "default"},
            )
        for index, child in enumerate(self._children):
            try:
                decision = await child.evaluate_policy(request)
            except Exception as exc:
                return PolicyDecision(
                    allowed=False,
                    reason=f"child {index} raised: {exc}",
                    metadata={
                        "policy": "composite",
                        "error_type": type(exc).__name__,
                        "decided_by": index,
                        "children": child_metadata,
                    },
                )
            child_metadata.append(
                {
                    "index": index,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "metadata": dict(decision.metadata),
                }
            )
            if self._mode == "all" and not decision.allowed:
                return PolicyDecision(
                    allowed=False,
                    reason=decision.reason,
                    metadata={"policy": "composite", "decided_by": index, "children": child_metadata},
                )
            if self._mode == "any" and decision.allowed:
                return PolicyDecision(
                    allowed=True,
                    reason=decision.reason,
                    metadata={"policy": "composite", "decided_by": index, "children": child_metadata},
                )
        if self._mode == "all":
            return PolicyDecision(
                allowed=True,
                metadata={"policy": "composite", "decided_by": "all_passed", "children": child_metadata},
            )
        last_reason = child_metadata[-1]["reason"] if child_metadata else "no policies allowed"
        return PolicyDecision(
            allowed=False,
            reason=last_reason,
            metadata={"policy": "composite", "decided_by": "none_allowed", "children": child_metadata},
        )
