from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from examples.pptx_generator.state import (
    DeckProject,
    IntentReport,
    Palette,
    SlideIR,
    SlideOutline,
    SlideSpec,
)


def test_intent_report_validates_purpose():
    with pytest.raises(ValidationError):
        IntentReport(
            topic="t",
            audience="a",
            purpose="bogus",
            tone="formal",
            slide_count_hint=5,
            required_sections=[],
            visuals_hint=[],
            research_queries=[],
            language="zh",
        )


def test_slide_count_bounds():
    with pytest.raises(ValidationError):
        IntentReport(
            topic="t",
            audience="a",
            purpose="pitch",
            tone="formal",
            slide_count_hint=25,
            required_sections=[],
            visuals_hint=[],
            research_queries=[],
            language="zh",
        )


def test_palette_hex_no_hash():
    with pytest.raises(ValidationError):
        Palette(primary="#123456", secondary="aabbcc", accent="aabbcc", light="aabbcc", bg="aabbcc")


def test_deck_project_minimum():
    p = DeckProject(slug="x", created_at=datetime.now(timezone.utc), stage="intent")
    assert p.slides == []
    assert p.intent is None


def test_slide_ir_freeform_requires_js():
    with pytest.raises(ValidationError):
        SlideIR(index=1, type="freeform", slots={}, freeform_js=None, generated_at=datetime.now(timezone.utc))


def test_slide_outline_indexes_unique():
    outline = SlideOutline(
        slides=[
            SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[]),
            SlideSpec(index=2, type="content", title="T", key_points=[], sources_cited=[]),
        ]
    )
    assert [s.index for s in outline.slides] == [1, 2]


def test_datetime_must_be_utc_aware():
    from datetime import datetime as _dt

    with pytest.raises(ValidationError):
        DeckProject(slug="x", created_at=_dt(2026, 4, 19), stage="intent")


def test_slide_ir_index_ge_1():
    with pytest.raises(ValidationError):
        SlideIR(index=0, type="cover", slots={"title": "T"}, generated_at=datetime.now(timezone.utc))


def test_slug_format_rejected():
    with pytest.raises(ValidationError):
        DeckProject(slug="Bad Slug", created_at=datetime.now(timezone.utc), stage="intent")


def test_palette_normalizes_to_lowercase():
    p = Palette(primary="AABBCC", secondary="aabbcc", accent="aabbcc", light="aabbcc", bg="aabbcc")
    assert p.primary == "aabbcc"


def test_slide_outline_duplicate_indexes_rejected():
    with pytest.raises(ValidationError):
        SlideOutline(
            slides=[
                SlideSpec(index=1, type="cover", title="T", key_points=[], sources_cited=[]),
                SlideSpec(index=1, type="content", title="T", key_points=[], sources_cited=[]),
            ]
        )
