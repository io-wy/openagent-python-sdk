from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import (
    DeckProject, IntentReport, ResearchFindings, SlideOutline, SlideSpec,
)
from examples.pptx_generator.wizard.outline import OutlineWizardStep


def _base_project():
    intent = IntentReport(
        topic="t", audience="a", purpose="pitch", tone="formal",
        slide_count_hint=3, required_sections=[], visuals_hint=[],
        research_queries=[], language="zh",
    )
    return DeckProject(
        slug="x", created_at=datetime.now(timezone.utc),
        stage="outline", intent=intent, research=ResearchFindings(),
    )


def _outline():
    return SlideOutline(slides=[
        SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[]),
        SlideSpec(index=2, type="content", title="Why", key_points=[], sources_cited=[]),
        SlideSpec(index=3, type="closing", title="End", key_points=[], sources_cited=[]),
    ])


@pytest.mark.asyncio
async def test_accepts_outline(monkeypatch):
    outline = _outline()
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=outline, state={"outline": outline.model_dump(mode="json")},
    )))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.outline.Wizard.select",
        AsyncMock(return_value="accept"),
    )
    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.outline and len(project.outline.slides) == 3
    assert project.stage == "theme"


@pytest.mark.asyncio
async def test_regenerates_on_retry(monkeypatch):
    outline = _outline()
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=outline, state={"outline": outline.model_dump(mode="json")},
    )))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.outline.Wizard.select",
        AsyncMock(return_value="regenerate"),
    )
    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "retry"
    assert project.stage == "outline"  # unchanged


@pytest.mark.asyncio
async def test_aborts(monkeypatch):
    outline = _outline()
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        parsed=outline, state={"outline": outline.model_dump(mode="json")},
    )))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.outline.Wizard.select",
        AsyncMock(return_value="abort"),
    )
    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"
