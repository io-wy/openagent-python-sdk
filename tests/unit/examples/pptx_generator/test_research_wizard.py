from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import (
    DeckProject, IntentReport, ResearchFindings, Source,
)
from examples.pptx_generator.wizard.research import ResearchWizardStep


def _intent(queries):
    return IntentReport(
        topic="t", audience="a", purpose="pitch", tone="formal",
        slide_count_hint=5, required_sections=[], visuals_hint=[],
        research_queries=queries, language="zh",
    )


def _project(intent):
    return DeckProject(
        slug="x", created_at=datetime.now(timezone.utc),
        stage="research", intent=intent,
    )


@pytest.mark.asyncio
async def test_skipped_when_no_queries():
    runtime = SimpleNamespace(run=AsyncMock())
    step = ResearchWizardStep(runtime=runtime)
    project = _project(_intent([]))
    result = await step.render(console=None, project=project)
    assert result.status == "skipped"
    assert project.research is not None
    assert project.research.sources == []
    assert project.stage == "outline"
    runtime.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_runs_agent_and_keeps_all(monkeypatch):
    findings = ResearchFindings(
        queries_executed=["q"],
        sources=[
            Source(url="https://a", title="A", snippet="sA"),
            Source(url="https://b", title="B", snippet="sB"),
        ],
        key_facts=["f1"],
        caveats=[],
    )
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=findings,
        state={"research": findings.model_dump(mode="json")},
    )))
    # User picks none → interpret as "keep all"
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.multi_select",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    step = ResearchWizardStep(runtime=runtime)
    project = _project(_intent(["q"]))
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert len(project.research.sources) == 2
    assert project.stage == "outline"


@pytest.mark.asyncio
async def test_captures_references_when_confirmed(monkeypatch):
    findings = ResearchFindings(
        queries_executed=["q"],
        sources=[Source(url="https://a", title="A", snippet="sA")],
        key_facts=[], caveats=[],
    )
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=findings, state={},
    )))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.multi_select",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.confirm",
        AsyncMock(return_value=True),
    )
    captures: list[tuple[str, str, str]] = []

    class FakeMem:
        def __init__(self, config=None):
            pass
        def capture(self, category, rule, reason):
            captures.append((category, rule, reason))
            return "id"

    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.MarkdownMemory", FakeMem,
    )
    step = ResearchWizardStep(runtime=runtime)
    await step.render(console=None, project=_project(_intent(["q"])))
    assert captures and captures[0][0] == "references"


@pytest.mark.asyncio
async def test_runs_agent_and_filters(monkeypatch):
    findings = ResearchFindings(
        queries_executed=["q"],
        sources=[
            Source(url="https://a", title="A", snippet="sA"),
            Source(url="https://b", title="B", snippet="sB"),
        ],
        key_facts=["f1"], caveats=[],
    )
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=findings,
        state={"research": findings.model_dump(mode="json")},
    )))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.multi_select",
        AsyncMock(return_value=["A"]),
    )
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.research.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    step = ResearchWizardStep(runtime=runtime)
    project = _project(_intent(["q"]))
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert len(project.research.sources) == 1
    assert project.research.sources[0].title == "A"


@pytest.mark.asyncio
async def test_fallback_to_state_dict_when_parsed_missing():
    findings = ResearchFindings(queries_executed=["q"], sources=[], key_facts=[], caveats=[])
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=None,
        state={"research": findings.model_dump(mode="json")},
    )))
    step = ResearchWizardStep(runtime=runtime)
    project = _project(_intent(["q"]))
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.research is not None
