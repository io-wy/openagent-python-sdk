"""Deterministic mock LLM provider for local development/tests."""

from __future__ import annotations

import json
from typing import Any

from openagents.llm.base import LLMClient


class MockLLMClient(LLMClient):
    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        _ = (model, temperature, max_tokens)
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                break

        parsed = self._parse_prompt(user_text)
        input_text = parsed.get("input", "")
        history_count = parsed.get("history_count", 0)

        if input_text.startswith("/tool"):
            rest = input_text[len("/tool") :].strip()
            if not rest:
                return json.dumps(
                    {"type": "final", "content": "Usage: /tool <tool_id> <query>"},
                    ensure_ascii=True,
                )
            parts = rest.split(maxsplit=1)
            tool_id = parts[0]
            query = parts[1] if len(parts) == 2 else ""
            return json.dumps(
                {"type": "tool_call", "tool": tool_id, "params": {"query": query}},
                ensure_ascii=True,
            )

        return json.dumps(
            {
                "type": "final",
                "content": f"Echo: {input_text} (history={history_count})",
            },
            ensure_ascii=True,
        )

    def _parse_prompt(self, text: str) -> dict[str, Any]:
        values: dict[str, Any] = {}
        in_history = False
        history_lines = []

        for line in text.splitlines():
            if line.startswith("CONVERSATION_HISTORY:") or line.startswith("HISTORY:"):
                in_history = True
                continue
            elif line.startswith("INPUT:") or line.startswith("AVAILABLE_TOOLS:"):
                in_history = False

            if in_history:
                if line.strip() and not line.startswith(" "):
                    # This is a new history entry marker
                    pass
                history_lines.append(line)
            elif line.startswith("INPUT:"):
                values["input"] = line[len("INPUT:") :].strip()
            elif line.startswith("HISTORY_COUNT:"):
                raw = line[len("HISTORY_COUNT:") :].strip()
                try:
                    values["history_count"] = int(raw)
                except ValueError:
                    values["history_count"] = 0

        # Count history items by counting "User:" markers (each user message = 1 history entry)
        history_count = sum(1 for line in history_lines if line.strip().startswith("User:"))
        values.setdefault("input", "")
        values.setdefault("history_count", history_count)
        return values

