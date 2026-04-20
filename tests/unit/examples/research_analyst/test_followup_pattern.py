from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examples.research_analyst.app.followup_pattern import FollowupFirstReActPattern


def _write_rules(tmp_path: Path, rules: list[dict[str, Any]]) -> Path:
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    return p


def _ctx(input_text: str, memory_view: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        input_text=input_text,
        memory_view=memory_view or {},
        state={},
        tools={},
    )


@pytest.mark.asyncio
async def test_resolver_resolves_when_rule_matches(tmp_path):
    rules = [
        {
            "name": "last_tools",
            "pattern": "which tools",
            "template": "last tools: {tool_ids}",
            "requires_history": True,
        }
    ]
    pattern = FollowupFirstReActPattern(config={"rules_file": str(_write_rules(tmp_path, rules))})
    ctx = _ctx(
        input_text="which tools were used",
        memory_view={
            "history": [
                {
                    "tool_results": [
                        {"tool_id": "read_file"},
                        {"tool_id": "write_file"},
                    ]
                }
            ]
        },
    )
    res = await pattern.resolve_followup(context=ctx)
    assert res is not None
    assert res.status == "resolved"
    assert "read_file" in str(res.output)
    assert "write_file" in str(res.output)


@pytest.mark.asyncio
async def test_resolver_none_when_no_rule_matches(tmp_path):
    rules = [
        {
            "name": "does_not_match",
            "pattern": "^nothingtoseehere$",
            "template": "x",
            "requires_history": False,
        }
    ]
    pattern = FollowupFirstReActPattern(config={"rules_file": str(_write_rules(tmp_path, rules))})
    ctx = _ctx(input_text="hello world")
    res = await pattern.resolve_followup(context=ctx)
    assert res is None


@pytest.mark.asyncio
async def test_resolver_abstains_when_history_required_but_missing(tmp_path):
    rules = [
        {
            "name": "needs_history",
            "pattern": "followup",
            "template": "last tools: {tool_ids}",
            "requires_history": True,
        }
    ]
    pattern = FollowupFirstReActPattern(config={"rules_file": str(_write_rules(tmp_path, rules))})
    ctx = _ctx(input_text="followup question", memory_view={})
    res = await pattern.resolve_followup(context=ctx)
    assert res is not None
    assert res.status == "abstain"


@pytest.mark.asyncio
async def test_inline_rules_supported(tmp_path):
    pattern = FollowupFirstReActPattern(
        config={
            "rules": [
                {
                    "name": "greet",
                    "pattern": "^hi$",
                    "template": "hello",
                    "requires_history": False,
                }
            ]
        }
    )
    ctx = _ctx(input_text="hi")
    res = await pattern.resolve_followup(context=ctx)
    assert res is not None
    assert res.status == "resolved"
    assert res.output == "hello"
