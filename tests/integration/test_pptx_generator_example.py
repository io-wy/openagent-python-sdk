"""End-to-end integration test for the pptx_generator wizard.

All external services (LLM, Tavily, shell) are mocked; the full 7-step
pipeline must run to completion and produce the expected output files.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.cli import run_wizard
from examples.pptx_generator.state import DeckProject


@pytest.mark.asyncio
async def test_end_to_end_all_stages_mocked(tmp_path, monkeypatch):
    """Run the full wizard with every external service mocked."""
    monkeypatch.setenv("PPTX_AGENT_OUTPUTS", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_API_BASE", "https://fake")
    monkeypatch.setenv("LLM_MODEL", "fake-model")

    # Mock every Wizard prompt to default accept path
    from openagents.cli import wizard as wiz

    monkeypatch.setattr(wiz.Wizard, "confirm", AsyncMock(return_value=True))
    monkeypatch.setattr(wiz.Wizard, "select", AsyncMock(return_value="accept"))
    monkeypatch.setattr(wiz.Wizard, "multi_select", AsyncMock(return_value=[]))
    monkeypatch.setattr(wiz.Wizard, "password", AsyncMock(return_value="sk-fake"))
    monkeypatch.setattr(wiz.Wizard, "text", AsyncMock(return_value=""))

    project = DeckProject(
        slug="inttest",
        created_at=datetime.now(timezone.utc),
        stage="intent",
    )

    # Build a fake runtime.run that dispatches by agent_id
    async def fake_runtime_run(*, agent_id, session_id, input_text, deps=None):
        from examples.pptx_generator.state import (
            FontPairing,
            IntentReport,
            Palette,
            ResearchFindings,
            SlideIR,
            SlideOutline,
            SlideSpec,
            ThemeSelection,
        )

        if agent_id == "intent-analyst":
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
            return SimpleNamespace(
                parsed=intent, state={"intent": intent.model_dump(mode="json")}
            )
        if agent_id == "research-agent":
            r = ResearchFindings()
            return SimpleNamespace(
                parsed=r, state={"research": r.model_dump(mode="json")}
            )
        if agent_id == "outliner":
            outline = SlideOutline(
                slides=[
                    SlideSpec(
                        index=1,
                        type="cover",
                        title="T",
                        key_points=[],
                        sources_cited=[],
                    ),
                    SlideSpec(
                        index=2,
                        type="content",
                        title="W",
                        key_points=[],
                        sources_cited=[],
                    ),
                    SlideSpec(
                        index=3,
                        type="closing",
                        title="E",
                        key_points=[],
                        sources_cited=[],
                    ),
                ]
            )
            return SimpleNamespace(
                parsed=outline,
                state={"outline": outline.model_dump(mode="json")},
            )
        if agent_id == "theme-selector":
            theme = ThemeSelection(
                palette=Palette(
                    primary="111111",
                    secondary="222222",
                    accent="333333",
                    light="444444",
                    bg="555555",
                ),
                fonts=FontPairing(
                    heading="Arial", body="Arial", cjk="Microsoft YaHei"
                ),
                style="sharp",
                page_badge_style="circle",
            )
            return SimpleNamespace(
                parsed=theme, state={"theme": theme.model_dump(mode="json")}
            )
        if agent_id == "slide-generator":
            payload = json.loads(input_text)
            i = payload["target_spec"]["index"]
            return SimpleNamespace(
                parsed=SlideIR(
                    index=i,
                    type=payload["target_spec"]["type"],
                    slots={"title": f"S{i}"},
                    generated_at=datetime.now(timezone.utc),
                )
            )
        raise AssertionError(f"unexpected agent {agent_id}")

    fake_runtime = SimpleNamespace(run=fake_runtime_run)
    fake_shell = SimpleNamespace(
        invoke=AsyncMock(
            return_value={
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "truncated": False,
            }
        )
    )

    rc = await run_wizard(project, runtime=fake_runtime, shell_tool=fake_shell)
    assert rc == 0
    # project.json saved
    assert (tmp_path / "inttest" / "project.json").exists()
    # slide file written
    assert (tmp_path / "inttest" / "slides" / "slide-01.js").exists()
    # compile.js written
    assert (tmp_path / "inttest" / "slides" / "compile.js").exists()
    # Final stage reached
    from examples.pptx_generator.persistence import load_project

    loaded = load_project("inttest", root=tmp_path)
    assert loaded.stage == "done"
