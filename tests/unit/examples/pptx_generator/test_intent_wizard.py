from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.state import DeckProject, IntentReport
from examples.pptx_generator.wizard.intent import IntentWizardStep


def _mk_report() -> IntentReport:
    return IntentReport(
        topic="t",
        audience="a",
        purpose="pitch",
        tone="formal",
        slide_count_hint=5,
        required_sections=[],
        visuals_hint=[],
        research_queries=[],
        language="zh",
    )


def _mk_project() -> DeckProject:
    return DeckProject(slug="x", created_at=datetime.now(timezone.utc), stage="intent")


def _patch_editor(monkeypatch: pytest.MonkeyPatch, actions: list[str]) -> list[IntentReport]:
    """Script the editor to return (report, next_action) pairs in order."""
    queue = list(actions)
    captured: list[IntentReport] = []

    async def fake_edit_intent(report: IntentReport):
        captured.append(report)
        action = queue.pop(0)
        return report, action

    monkeypatch.setattr("examples.pptx_generator.wizard.intent.edit_intent", fake_edit_intent)
    return captured


@pytest.mark.asyncio
async def test_intent_wizard_confirm_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _mk_report()
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(parsed=report, state={})))
    _patch_editor(monkeypatch, actions=["confirm"])
    # memory save? no
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.intent.Wizard.confirm",
        AsyncMock(return_value=False),
    )

    step = IntentWizardStep(runtime=runtime, topic_hint="draft")
    project = _mk_project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.intent == report
    assert project.stage == "env"


@pytest.mark.asyncio
async def test_intent_wizard_regenerate_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    report_v1 = _mk_report()
    report_v2 = report_v1.model_copy(update={"topic": "new-topic"})
    runtime = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                SimpleNamespace(parsed=report_v1, state={}),
                SimpleNamespace(parsed=report_v2, state={}),
            ]
        )
    )
    _patch_editor(monkeypatch, actions=["regenerate", "confirm"])
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.intent.Wizard.confirm",
        AsyncMock(return_value=False),
    )

    step = IntentWizardStep(runtime=runtime, topic_hint="draft")
    project = _mk_project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.intent == report_v2
    assert runtime.run.await_count == 2


@pytest.mark.asyncio
async def test_intent_wizard_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(parsed=_mk_report(), state={})))
    _patch_editor(monkeypatch, actions=["abort"])

    step = IntentWizardStep(runtime=runtime)
    project = _mk_project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"
    assert project.intent is None
    assert project.stage == "intent"


@pytest.mark.asyncio
async def test_intent_wizard_saves_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _mk_report()
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(parsed=report, state={})))
    _patch_editor(monkeypatch, actions=["confirm"])
    # user confirms memory save
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.intent.Wizard.confirm",
        AsyncMock(return_value=True),
    )

    capture_log: list[tuple[str, str, str]] = []

    class FakeMem:
        def __init__(self, config=None):
            self.config = config

        def capture(self, category: str, rule: str, reason: str) -> str:
            capture_log.append((category, rule, reason))
            return "xyz"

    monkeypatch.setattr("examples.pptx_generator.wizard.intent.MarkdownMemory", FakeMem)

    step = IntentWizardStep(runtime=runtime, topic_hint="draft")
    result = await step.render(console=None, project=_mk_project())
    assert result.status == "completed"
    assert len(capture_log) == 1
    assert capture_log[0][0] == "user_goals"
    assert "tone=formal" in capture_log[0][1]
