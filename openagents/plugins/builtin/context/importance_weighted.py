"""Importance-weighted token-budget context assembler."""

from __future__ import annotations

from typing import Any

from openagents.plugins.builtin.context.base import TokenBudgetContextAssembler


class ImportanceWeightedContextAssembler(TokenBudgetContextAssembler):
    """Keep high-importance messages first, fill remaining budget chronologically.

    Priority order (higher score = kept first):

    1. The first ``role=system`` message (baseline instructions)
    2. The most recent ``role=user`` message
    3. The most recent ``role=tool`` message
    4. Other recent messages

    Chronological order is preserved in the returned transcript.
    """

    def _score(self, index: int, msg: dict[str, Any], total: int) -> float:
        role = msg.get("role")
        if role == "system" and index == 0:
            return 1000.0
        if role == "tool":
            return 900.0 - (total - index)
        if role == "user":
            return 800.0 - (total - index)
        if role == "assistant":
            return 500.0 - (total - index)
        return 100.0 - (total - index)

    def _trim_by_budget(
        self,
        llm_client: Any,
        msgs: list[dict[str, Any]],
        budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if not msgs:
            return [], 0

        scored = sorted(
            ((i, m, self._score(i, m, len(msgs))) for i, m in enumerate(msgs)),
            key=lambda t: t[2],
            reverse=True,
        )
        kept_indices: set[int] = set()
        remaining = budget
        for i, m, _ in scored:
            cost = self._measure(llm_client, m)
            if cost > remaining:
                continue
            kept_indices.add(i)
            remaining -= cost

        kept = [m for i, m in enumerate(msgs) if i in kept_indices]
        return kept, len(msgs) - len(kept)
