"""App-layer pattern: rule-based follow-up resolution via ``resolve_followup()``.

With the consolidated seam API, follow-up resolution is implemented by
overriding ``PatternPlugin.resolve_followup()`` (the builtin ``ReActPattern``
calls this before its LLM loop; returning ``FollowupResolution(status="resolved",
...)`` short-circuits the loop and skips the LLM entirely).

This example pattern loads a list of regex-to-template rules from a JSON file
and matches the most recent user message against them — if any rule fires and
memory has history, the interpolated template is returned directly.

See ``docs/seams-and-extension-points.md`` for the rationale behind making
follow-up resolution a pattern method rather than a separate seam.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents.errors.exceptions import PluginLoadError
from openagents.interfaces.followup import FollowupResolution
from openagents.interfaces.run_context import RunContext
from openagents.plugins.builtin.pattern.react import ReActPattern


class _Rule(BaseModel):
    name: str
    pattern: str
    template: str
    requires_history: bool = True


class FollowupFirstReActPattern(ReActPattern):
    """ReAct variant that resolves obvious follow-ups from rules before the LLM.

    Subclasses ``ReActPattern`` and overrides ``resolve_followup()`` — when the
    most recent user message matches one of the configured regex rules (and
    there is history in memory), the rule's template is rendered and returned,
    which causes ``ReActPattern.execute()`` to short-circuit without invoking
    the LLM.

    Config:
        - ``rules``: list of ``{name, pattern, template, requires_history}``
        - ``rules_file``: path to a JSON file containing the same shape

    Rules from ``rules_file`` are evaluated before inline ``rules``.
    """

    class Config(ReActPattern.Config):
        rules_file: str | None = None
        rules: list[dict[str, Any]] = Field(default_factory=list)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        cfg = self.config or {}
        rules_file = cfg.get("rules_file")
        inline_rules = cfg.get("rules") or []
        file_rules: list[_Rule] = []
        if rules_file:
            path = Path(rules_file)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PluginLoadError(
                    f"FollowupFirstReActPattern: could not read rules_file '{rules_file}': {exc}"
                ) from exc
            if not isinstance(raw, list):
                raise PluginLoadError(f"FollowupFirstReActPattern: rules_file '{rules_file}' must be a JSON array")
            for item in raw:
                file_rules.append(_Rule.model_validate(item))
        all_rules = [*file_rules, *[_Rule.model_validate(r) for r in inline_rules]]
        self._rules: list[tuple[_Rule, re.Pattern[str]]] = []
        for r in all_rules:
            try:
                compiled = re.compile(r.pattern, re.IGNORECASE)
            except re.error as exc:
                raise PluginLoadError(f"FollowupFirstReActPattern: invalid pattern in rule '{r.name}': {exc}") from exc
            self._rules.append((r, compiled))

    async def resolve_followup(self, *, context: RunContext[Any]) -> FollowupResolution | None:
        text = str(getattr(context, "input_text", "") or "")
        for rule, compiled in self._rules:
            if not compiled.search(text):
                continue
            memory_view = getattr(context, "memory_view", {}) or {}
            history = memory_view.get("history") if isinstance(memory_view, dict) else None
            if rule.requires_history and (not isinstance(history, list) or not history):
                return FollowupResolution(
                    status="abstain",
                    reason="no history",
                    metadata={"rule": rule.name},
                )
            last = history[-1] if isinstance(history, list) and history else {}
            last = last if isinstance(last, dict) else {}
            tool_ids: list[str] = []
            raw_tool_results = last.get("tool_results")
            if isinstance(raw_tool_results, list):
                for item in raw_tool_results:
                    if isinstance(item, dict) and isinstance(item.get("tool_id"), str):
                        tool_ids.append(item["tool_id"])
            mapping = collections.defaultdict(
                str,
                {
                    "tool_ids": ", ".join(tool_ids),
                    "last_input": str(last.get("input", "")),
                    "last_output": str(last.get("output", "")),
                },
            )
            rendered = rule.template.format_map(mapping)
            return FollowupResolution(
                status="resolved",
                output=rendered,
                metadata={"rule": rule.name},
            )
        return None
