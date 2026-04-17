"""Head+tail token-budget context assembler."""

from __future__ import annotations

from typing import Any

from openagents.plugins.builtin.context.base import TokenBudgetContextAssembler


class HeadTailContextAssembler(TokenBudgetContextAssembler):
    """Keep the first N messages and as many trailing messages as budget allows.

    Useful when the transcript opens with a system prompt or task statement
    worth preserving unconditionally while also keeping the recent tail.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config)
        self._head_messages = int(self.config.get("head_messages", 3))

    def _trim_by_budget(
        self,
        llm_client: Any,
        msgs: list[dict[str, Any]],
        budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if not msgs:
            return [], 0

        head = msgs[: self._head_messages]
        head_tokens = sum(self._measure(llm_client, m) for m in head)
        remaining = max(0, budget - head_tokens)

        tail: list[dict[str, Any]] = []
        for m in reversed(msgs[self._head_messages:]):
            cost = self._measure(llm_client, m)
            if cost > remaining:
                break
            tail.append(m)
            remaining -= cost
        tail.reverse()

        omitted_count = max(0, len(msgs) - len(head) - len(tail))
        if omitted_count > 0:
            summary = {
                "role": "system",
                "content": f"Summary: omitted {omitted_count} message(s) from the middle.",
            }
            return head + [summary] + tail, omitted_count
        return head + tail, 0
