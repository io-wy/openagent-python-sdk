from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import (
    DeckProject,
    FontPairing,
    IntentReport,
    Palette,
    ResearchFindings,
    SlideIR,
    SlideOutline,
    SlideSpec,
    ThemeSelection,
)
from examples.pptx_generator.wizard.slides import SlideGeneratorWizardStep


def _base_project(n: int = 3) -> DeckProject:
    specs = [
        SlideSpec(index=i, type="content", title=f"S{i}", key_points=["a"], sources_cited=[])
        for i in range(1, n + 1)
    ]
    return DeckProject(
        slug="x", created_at=datetime.now(timezone.utc), stage="slides",
        intent=IntentReport(
            topic="t", audience="a", purpose="pitch", tone="formal",
            slide_count_hint=n, required_sections=[], visuals_hint=[],
            research_queries=[], language="zh",
        ),
        research=ResearchFindings(),
        outline=SlideOutline(slides=specs),
        theme=ThemeSelection(
            palette=Palette(
                primary="111111", secondary="222222", accent="333333",
                light="444444", bg="555555",
            ),
            fonts=FontPairing(heading="Arial", body="Arial", cjk="Microsoft YaHei"),
            style="sharp",
            page_badge_style="circle",
        ),
    )


def _valid_ir(idx: int) -> SlideIR:
    return SlideIR(
        index=idx,
        type="content",
        slots={"title": f"S{idx}", "body_blocks": [{"kind": "bullets", "items": ["a"]}]},
        generated_at=datetime.now(timezone.utc),
    )


def _invalid_ir(idx: int) -> SlideIR:
    return SlideIR(
        index=idx,
        type="content",
        slots={},  # missing title/body_blocks
        generated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_generates_all_slides_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(*, agent_id, session_id, input_text, deps=None):
        payload = json.loads(input_text)
        i = payload["target_spec"]["index"]
        return SimpleNamespace(parsed=_valid_ir(i))

    runtime = SimpleNamespace(run=AsyncMock(side_effect=fake_run))
    # skip the interactive "save as preference" prompt
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    step = SlideGeneratorWizardStep(runtime=runtime, concurrency=3)
    project = _base_project(n=3)
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert [s.index for s in project.slides] == [1, 2, 3]
    assert project.stage == "compile"


@pytest.mark.asyncio
async def test_slides_sorted_when_async_out_of_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*, agent_id, session_id, input_text, deps=None):
        payload = json.loads(input_text)
        i = payload["target_spec"]["index"]
        await asyncio.sleep(0.001 * (5 - i))
        return SimpleNamespace(parsed=_valid_ir(i))

    runtime = SimpleNamespace(run=AsyncMock(side_effect=fake_run))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    step = SlideGeneratorWizardStep(runtime=runtime, concurrency=4)
    project = _base_project(n=4)
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert [s.index for s in project.slides] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counts: dict[int, int] = {}

    async def fake_run(*, agent_id, session_id, input_text, deps=None):
        payload = json.loads(input_text)
        idx = payload["target_spec"]["index"]
        call_counts[idx] = call_counts.get(idx, 0) + 1
        if idx == 2 and call_counts[idx] == 1:
            return SimpleNamespace(parsed=_invalid_ir(idx))
        return SimpleNamespace(parsed=_valid_ir(idx))

    runtime = SimpleNamespace(run=AsyncMock(side_effect=fake_run))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.select",
        AsyncMock(return_value="continue"),
    )

    step = SlideGeneratorWizardStep(runtime=runtime, concurrency=2)
    project = _base_project(n=3)
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert call_counts[2] == 2


@pytest.mark.asyncio
async def test_fallback_to_freeform_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*, agent_id, session_id, input_text, deps=None):
        payload = json.loads(input_text)
        idx = payload["target_spec"]["index"]
        if idx == 3:
            return SimpleNamespace(parsed=_invalid_ir(idx))
        return SimpleNamespace(parsed=_valid_ir(idx))

    runtime = SimpleNamespace(run=AsyncMock(side_effect=fake_run))
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.confirm",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.slides.Wizard.select",
        AsyncMock(return_value="continue"),
    )

    step = SlideGeneratorWizardStep(runtime=runtime, concurrency=1)
    project = _base_project(n=3)
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert len(project.slides) == 3
    # Slide 3 should have fallen back to freeform
    fallback = next(s for s in project.slides if s.index == 3)
    assert fallback.type == "freeform"
    assert fallback.freeform_js is not None
