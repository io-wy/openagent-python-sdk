from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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
from examples.pptx_generator.wizard.compile_qa import CompileQAWizardStep


def _project(n=1):
    theme = ThemeSelection(
        palette=Palette(primary="111111", secondary="222222", accent="333333", light="444444", bg="555555"),
        fonts=FontPairing(heading="Arial", body="Arial", cjk="Microsoft YaHei"),
        style="sharp",
        page_badge_style="circle",
    )
    slides = [
        SlideIR(
            index=i,
            type="cover" if i == 1 else "content",
            slots={"title": f"S{i}"},
            generated_at=datetime.now(timezone.utc),
        )
        for i in range(1, n + 1)
    ]
    outline_specs = [
        SlideSpec(index=i, type=s.type, title=f"S{i}", key_points=[], sources_cited=[])
        for i, s in enumerate(slides, start=1)
    ]
    return DeckProject(
        slug="x",
        created_at=datetime.now(timezone.utc),
        stage="compile",
        intent=IntentReport(
            topic="t",
            audience="a",
            purpose="pitch",
            tone="formal",
            slide_count_hint=max(3, n),
            required_sections=[],
            visuals_hint=[],
            research_queries=[],
            language="zh",
        ),
        research=ResearchFindings(),
        outline=SlideOutline(slides=outline_specs),
        theme=theme,
        slides=slides,
    )


@pytest.mark.asyncio
async def test_writes_slide_files_and_compiles(tmp_path, monkeypatch):
    calls = []

    async def fake_invoke(params, context=None):
        calls.append(list(params.get("command", [])))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "truncated": False}

    tool = SimpleNamespace(invoke=fake_invoke)
    # Ensure markitdown is seen as absent so we don't hit a real binary lookup
    monkeypatch.setattr("shutil.which", lambda name: None)

    step = CompileQAWizardStep(
        shell_tool=tool,
        output_root=tmp_path,
        templates_dir=Path("examples/pptx_generator/templates"),
    )
    project = _project(n=3)
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    # Slide files should be written
    slides_dir = tmp_path / project.slug / "slides"
    for i in range(1, 4):
        slide_file = slides_dir / f"slide-{i:02d}.js"
        assert slide_file.exists(), f"missing {slide_file}"
    # compile.js + package.json written
    assert (slides_dir / "compile.js").exists()
    assert (slides_dir / "package.json").exists()
    # Expected commands: npm install + node compile.js
    flat_calls = [" ".join(c) for c in calls]
    assert any("npm" in c and "install" in c for c in flat_calls)
    assert any(c.startswith("node") for c in flat_calls)
    assert project.stage == "done"


@pytest.mark.asyncio
async def test_freeform_slide_uses_verbatim_js(tmp_path, monkeypatch):
    async def fake_invoke(params, context=None):
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "truncated": False}

    monkeypatch.setattr("shutil.which", lambda name: None)

    project = _project(n=1)
    # Replace the first slide with a freeform
    project.slides[0] = SlideIR(
        index=1,
        type="freeform",
        slots={},
        freeform_js="// custom JS\nmodule.exports = { createSlide: () => {} };\n",
        generated_at=datetime.now(timezone.utc),
    )
    project.outline.slides[0] = SlideSpec(index=1, type="freeform", title="F", key_points=[], sources_cited=[])

    step = CompileQAWizardStep(
        shell_tool=SimpleNamespace(invoke=fake_invoke),
        output_root=tmp_path,
        templates_dir=Path("examples/pptx_generator/templates"),
    )
    await step.render(console=None, project=project)
    slide_file = tmp_path / project.slug / "slides" / "slide-01.js"
    text = slide_file.read_text(encoding="utf-8")
    assert "custom JS" in text


@pytest.mark.asyncio
async def test_loopback_to_slides_when_compile_fails(tmp_path, monkeypatch):
    async def fake_invoke(params, context=None):
        cmd = list(params.get("command", []))
        # Fail only on `node compile.js`
        if cmd and cmd[0] == "node":
            return {"exit_code": 1, "stdout": "", "stderr": "boom"}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "examples.pptx_generator.wizard.compile_qa.Wizard.select",
        AsyncMock(return_value="go back to slides"),
    )
    step = CompileQAWizardStep(
        shell_tool=SimpleNamespace(invoke=fake_invoke),
        output_root=tmp_path,
        templates_dir=Path("examples/pptx_generator/templates"),
    )
    project = _project(n=1)
    project.slides = [
        SlideIR(
            index=1,
            type="cover",
            slots={"title": "T"},
            generated_at=datetime.now(timezone.utc),
        )
    ]
    project.outline.slides = [SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[])]
    result = await step.render(console=None, project=project)
    assert result.status == "retry"
    assert project.stage == "slides"


@pytest.mark.asyncio
async def test_runs_markitdown_when_available(tmp_path, monkeypatch):
    calls = []

    async def fake_invoke(params, context=None):
        calls.append(list(params.get("command", [])))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "truncated": False}

    # Simulate markitdown is on PATH
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/markitdown" if name == "markitdown" else None)
    step = CompileQAWizardStep(
        shell_tool=SimpleNamespace(invoke=fake_invoke),
        output_root=tmp_path,
        templates_dir=Path("examples/pptx_generator/templates"),
    )
    project = _project(n=1)
    project.slides = [SlideIR(index=1, type="cover", slots={"title": "T"}, generated_at=datetime.now(timezone.utc))]
    project.outline.slides = [SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[])]
    await step.render(console=None, project=project)
    flat = [" ".join(c) for c in calls]
    assert any(c.startswith("markitdown") for c in flat)
