from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import (
    DeckProject,
    IntentReport,
    ResearchFindings,
    SlideOutline,
    SlideSpec,
)
from examples.pptx_generator.wizard.outline import OutlineWizardStep


def _base_project() -> DeckProject:
    intent = IntentReport(
        topic="t",
        audience="a",
        purpose="pitch",
        tone="formal",
        slide_count_hint=3,
        required_sections=[],
        visuals_hint=[],
        research_queries=[],
        language="zh",
    )
    return DeckProject(
        slug="x",
        created_at=datetime.now(timezone.utc),
        stage="outline",
        intent=intent,
        research=ResearchFindings(),
    )


def _outline() -> SlideOutline:
    return SlideOutline(
        slides=[
            SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[]),
            SlideSpec(index=2, type="content", title="Why", key_points=[], sources_cited=[]),
            SlideSpec(index=3, type="closing", title="End", key_points=[], sources_cited=[]),
        ]
    )


def _patch_editor(monkeypatch: pytest.MonkeyPatch, updates: list[tuple[SlideOutline, str]]) -> None:
    queue = list(updates)

    async def fake_edit(outline: SlideOutline):
        return queue.pop(0)

    monkeypatch.setattr("examples.pptx_generator.wizard.outline.edit_outline", fake_edit)


@pytest.mark.asyncio
async def test_accepts_outline(monkeypatch: pytest.MonkeyPatch) -> None:
    outline = _outline()
    runtime = SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                parsed=outline,
                state={"outline": outline.model_dump(mode="json")},
            )
        )
    )
    _patch_editor(monkeypatch, [(outline, "accept")])

    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.outline and len(project.outline.slides) == 3
    assert project.stage == "theme"


@pytest.mark.asyncio
async def test_regenerates_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    outline = _outline()
    runtime = SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                parsed=outline,
                state={"outline": outline.model_dump(mode="json")},
            )
        )
    )
    _patch_editor(monkeypatch, [(outline, "regenerate")])

    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "retry"
    assert project.stage == "outline"


@pytest.mark.asyncio
async def test_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    outline = _outline()
    runtime = SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                parsed=outline,
                state={"outline": outline.model_dump(mode="json")},
            )
        )
    )
    _patch_editor(monkeypatch, [(outline, "abort")])

    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"


@pytest.mark.asyncio
async def test_edit_outline_returns_mutated_value(monkeypatch: pytest.MonkeyPatch) -> None:
    outline = _outline()
    # Simulate the editor dropping slide 2
    pruned = SlideOutline(slides=[outline.slides[0], outline.slides[2].model_copy(update={"index": 2})])
    runtime = SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                parsed=outline,
                state={"outline": outline.model_dump(mode="json")},
            )
        )
    )
    _patch_editor(monkeypatch, [(pruned, "accept")])

    step = OutlineWizardStep(runtime=runtime)
    project = _base_project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.outline is not None
    assert [s.title for s in project.outline.slides] == ["T", "End"]
