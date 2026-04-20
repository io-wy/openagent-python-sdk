from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import (
    DeckProject,
    FontPairing,
    IntentReport,
    Palette,
    ThemeCandidateList,
    ThemeSelection,
)
from examples.pptx_generator.wizard.theme import ThemeWizardStep


def _theme(primary: str = "111111") -> ThemeSelection:
    return ThemeSelection(
        palette=Palette(
            primary=primary,
            secondary="222222",
            accent="333333",
            light="444444",
            bg="555555",
        ),
        fonts=FontPairing(heading="Arial", body="Arial", cjk="Microsoft YaHei"),
        style="sharp",
        page_badge_style="circle",
    )


def _bundle() -> ThemeCandidateList:
    return ThemeCandidateList(candidates=[_theme("aa1111"), _theme("bb2222"), _theme("cc3333")])


def _project() -> DeckProject:
    return DeckProject(
        slug="x",
        created_at=datetime.now(timezone.utc),
        stage="theme",
        intent=IntentReport(
            topic="t",
            audience="a",
            purpose="pitch",
            tone="formal",
            slide_count_hint=5,
            required_sections=[],
            visuals_hint=[],
            research_queries=[],
            language="zh",
        ),
    )


def _patch_select(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    q = list(answers)

    async def fake_select(prompt: str, choices: list[str], default: str | None = None) -> str:
        return q.pop(0)

    monkeypatch.setattr("examples.pptx_generator.wizard.theme.Wizard.select", fake_select)


def _runtime_returning(bundle: ThemeCandidateList) -> SimpleNamespace:
    return SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(parsed=bundle, state={})))


@pytest.mark.asyncio
async def test_pick_first_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["pick 1"])
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.theme.Wizard.confirm",
        AsyncMock(return_value=False),
    )

    step = ThemeWizardStep(runtime=runtime)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.theme == bundle.candidates[0]
    assert project.stage == "slides"


@pytest.mark.asyncio
async def test_pick_second_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["pick 2"])
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.theme.Wizard.confirm",
        AsyncMock(return_value=False),
    )

    step = ThemeWizardStep(runtime=runtime)
    project = _project()
    await step.render(console=None, project=project)
    assert project.theme == bundle.candidates[1]


@pytest.mark.asyncio
async def test_regenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["regenerate"])

    step = ThemeWizardStep(runtime=runtime)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "retry"
    assert project.stage == "theme"


@pytest.mark.asyncio
async def test_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["abort"])

    step = ThemeWizardStep(runtime=runtime)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"


@pytest.mark.asyncio
async def test_custom_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["custom editor"])
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.theme.Wizard.confirm",
        AsyncMock(return_value=False),
    )

    custom_theme = _theme(primary="ff00ff")

    async def fake_edit(base):
        return custom_theme

    monkeypatch.setattr("examples.pptx_generator.wizard.theme.edit_theme_custom", fake_edit)

    step = ThemeWizardStep(runtime=runtime)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.theme == custom_theme


@pytest.mark.asyncio
async def test_memory_capture_when_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle()
    runtime = _runtime_returning(bundle)
    _patch_select(monkeypatch, ["pick 1"])
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.theme.Wizard.confirm",
        AsyncMock(return_value=True),
    )

    captures: list[tuple[str, str, str]] = []

    class FakeMem:
        def __init__(self, config=None):
            pass

        def capture(self, category: str, rule: str, reason: str) -> str:
            captures.append((category, rule, reason))
            return "zzz"

    monkeypatch.setattr("examples.pptx_generator.wizard.theme.MarkdownMemory", FakeMem)

    step = ThemeWizardStep(runtime=runtime)
    await step.render(console=None, project=_project())
    assert captures and captures[0][0] == "decisions"


@pytest.mark.asyncio
async def test_extracts_single_theme_as_degenerate_bundle() -> None:
    # Back-compat path: the agent returns a plain ThemeSelection
    theme = _theme()
    out = ThemeWizardStep._extract(theme)
    assert isinstance(out, ThemeCandidateList)
    assert len(out.candidates) >= 3
