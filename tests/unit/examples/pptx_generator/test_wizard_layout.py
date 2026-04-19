"""Unit tests for examples/pptx_generator/wizard/_layout.py."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from examples.pptx_generator.wizard._layout import (
    STAGES,
    LayoutRenderer,
    LogRing,
    _sidebar_glyph,
    _sidebar_stage_number,
)


def make_project(stage: str, slug: str = "demo-deck") -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        stage=stage,
        created_at=datetime(2026, 4, 19, tzinfo=timezone.utc),
    )


class TestLogRing:
    def test_truncates_to_max_lines(self) -> None:
        ring = LogRing(max_lines=3)
        for i in range(6):
            ring.append(f"line-{i}")
        assert ring.snapshot() == ["line-3", "line-4", "line-5"]

    def test_empty_by_default(self) -> None:
        ring = LogRing(max_lines=5)
        assert ring.snapshot() == []

    def test_clear_resets(self) -> None:
        ring = LogRing(max_lines=2)
        ring.append("a")
        ring.append("b")
        ring.clear()
        assert ring.snapshot() == []


class TestSidebarGlyphs:
    @pytest.mark.parametrize(
        "project_stage,row_stage,expected",
        [
            ("intent", "intent", "▶"),
            ("intent", "env", "○"),
            ("env", "intent", "✓"),
            ("env", "env", "▶"),
            ("theme", "intent", "✓"),
            ("theme", "env", "✓"),
            ("theme", "research", "✓"),
            ("theme", "outline", "✓"),
            ("theme", "theme", "▶"),
            ("theme", "slides", "○"),
            ("done", "intent", "✓"),
            ("done", "compile", "✓"),
        ],
    )
    def test_glyph(self, project_stage: str, row_stage: str, expected: str) -> None:
        assert _sidebar_glyph(project_stage, row_stage) == expected

    def test_unknown_stage_returns_pending(self) -> None:
        assert _sidebar_glyph("nonsense", "intent") == "○"


class TestStageNumber:
    @pytest.mark.parametrize(
        "stage,expected",
        [
            ("intent", 1),
            ("env", 2),
            ("research", 3),
            ("outline", 4),
            ("theme", 5),
            ("slides", 6),
            ("compile", 7),
            ("done", 7),
        ],
    )
    def test_number(self, stage: str, expected: int) -> None:
        assert _sidebar_stage_number(stage) == expected


class TestLayoutRenderer:
    def test_sidebar_entries_every_stage(self) -> None:
        for project_stage, _ in STAGES:
            project = make_project(project_stage)
            renderer = LayoutRenderer(project=project)
            entries = renderer.sidebar_entries()
            assert len(entries) == len(STAGES)
            # exactly one ▶ when not done
            running_count = sum(1 for e in entries if e.startswith("▶"))
            assert running_count == 1

    def test_sidebar_entries_done_has_all_checked(self) -> None:
        renderer = LayoutRenderer(project=make_project("done"))
        entries = renderer.sidebar_entries()
        assert all(e.startswith("✓") for e in entries)

    def test_status_bar_contains_slug_and_stage(self) -> None:
        renderer = LayoutRenderer(project=make_project("research", slug="my-slug-123"))
        text = renderer.status_bar_text()
        assert "my-slug-123" in text
        assert "stage 3/7" in text

    def test_status_bar_elapsed_is_mmss(self) -> None:
        renderer = LayoutRenderer(project=make_project("intent"))
        text = renderer.status_bar_text()
        # looks like "... · MM:SS"
        assert text.rsplit("· ", maxsplit=1)[-1].count(":") == 1

    def test_build_returns_layout_when_rich_present(self) -> None:
        renderer = LayoutRenderer(project=make_project("outline"))
        layout = renderer.build()
        # rich is a required extra for pptx; layout should render
        assert layout is not None

    def test_render_swaps_project(self) -> None:
        renderer = LayoutRenderer(project=make_project("intent"))
        layout_a = renderer.render()
        assert renderer.project.stage == "intent"
        renderer.render(project=make_project("theme"))
        assert renderer.project.stage == "theme"
        assert layout_a is not None
