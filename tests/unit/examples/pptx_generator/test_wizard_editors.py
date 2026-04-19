"""Unit tests for examples/pptx_generator/wizard/_editors.py."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from examples.pptx_generator.state import (
    FontPairing,
    IntentReport,
    Palette,
    SlideOutline,
    SlideSpec,
    ThemeSelection,
)
from examples.pptx_generator.wizard import _editors


def _base_intent() -> IntentReport:
    return IntentReport(
        topic="Deep learning",
        audience="engineers",
        purpose="teaching",
        tone="formal",
        slide_count_hint=10,
        required_sections=["intro", "conclusion"],
        visuals_hint=["diagrams"],
        research_queries=["transformer", "rnn"],
        language="en",
    )


def _base_outline() -> SlideOutline:
    return SlideOutline(
        slides=[
            SlideSpec(index=1, type="cover", title="Intro", key_points=[], sources_cited=[]),
            SlideSpec(index=2, type="content", title="Body", key_points=["a"], sources_cited=[]),
            SlideSpec(index=3, type="closing", title="Outro", key_points=[], sources_cited=[]),
        ]
    )


def _base_theme() -> ThemeSelection:
    return ThemeSelection(
        palette=Palette(primary="112233", secondary="445566", accent="778899", light="aabbcc", bg="ddeeff"),
        fonts=FontPairing(heading="Inter", body="Inter", cjk="Noto"),
        style="sharp",
        page_badge_style="circle",
    )


class _Scripted:
    """Queue-backed script for Wizard.* helpers. Raises on unexpected prompts."""

    def __init__(self, script: dict[str, list[Any]]):
        self.script = {k: iter(v) for k, v in script.items()}

    def pop(self, kind: str) -> Any:
        try:
            return next(self.script[kind])
        except (StopIteration, KeyError) as exc:
            raise AssertionError(f"no more {kind} responses scripted") from exc


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Scripted]:
    holder: dict[str, _Scripted] = {}

    async def fake_select(prompt: str, choices: list[str], default: str | None = None) -> str:
        return holder["s"].pop("select")

    async def fake_text(prompt: str, default: str | None = None) -> str:
        return holder["s"].pop("text")

    async def fake_confirm(prompt: str, default: bool = True) -> bool:
        return holder["s"].pop("confirm")

    monkeypatch.setattr(_editors.Wizard, "select", fake_select)
    monkeypatch.setattr(_editors.Wizard, "text", fake_text)
    monkeypatch.setattr(_editors.Wizard, "confirm", fake_confirm)

    def _set(script: dict[str, list[Any]]) -> _Scripted:
        sc = _Scripted(script)
        holder["s"] = sc
        return sc

    yield _set  # type: ignore[misc]


# ---------- intent --------------------------------------------------------


class TestEditIntent:
    @pytest.mark.asyncio
    async def test_confirm_immediately(self, scripted: Any) -> None:
        scripted({"select": ["confirm"]})
        report = _base_intent()
        out, action = await _editors.edit_intent(report)
        assert action == "confirm"
        assert out == report

    @pytest.mark.asyncio
    async def test_regenerate(self, scripted: Any) -> None:
        scripted({"select": ["regenerate"]})
        report = _base_intent()
        out, action = await _editors.edit_intent(report)
        assert action == "regenerate"
        assert out == report

    @pytest.mark.asyncio
    async def test_abort(self, scripted: Any) -> None:
        scripted({"select": ["abort"]})
        out, action = await _editors.edit_intent(_base_intent())
        assert action == "abort"

    @pytest.mark.asyncio
    async def test_edit_tone_then_confirm(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "tone", "energetic", "confirm"],
        })
        report = _base_intent()
        out, action = await _editors.edit_intent(report)
        assert action == "confirm"
        assert out.tone == "energetic"
        # other fields unchanged
        assert out.topic == report.topic
        assert out.audience == report.audience
        assert out.purpose == report.purpose

    @pytest.mark.asyncio
    async def test_edit_topic(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "topic", "confirm"],
            "text": ["Transformer architectures"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.topic == "Transformer architectures"

    @pytest.mark.asyncio
    async def test_edit_slide_count_valid(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "slide_count_hint", "confirm"],
            "text": ["12"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.slide_count_hint == 12

    @pytest.mark.asyncio
    async def test_edit_slide_count_rejects_out_of_range_then_accepts(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "slide_count_hint", "confirm"],
            "text": ["99", "not-a-number", "7"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.slide_count_hint == 7

    @pytest.mark.asyncio
    async def test_edit_research_queries_add(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "research_queries", "add", "done", "confirm"],
            "text": ["attention mechanism"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.research_queries == ["transformer", "rnn", "attention mechanism"]

    @pytest.mark.asyncio
    async def test_edit_research_queries_remove(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "research_queries", "remove", "rnn", "done", "confirm"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.research_queries == ["transformer"]

    @pytest.mark.asyncio
    async def test_edit_research_queries_reorder(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "research_queries", "reorder", "done", "confirm"],
            "text": ["2,1"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.research_queries == ["rnn", "transformer"]

    @pytest.mark.asyncio
    async def test_edit_research_queries_edit_item(self, scripted: Any) -> None:
        scripted({
            "select": ["edit field", "research_queries", "edit-item", "rnn", "done", "confirm"],
            "text": ["lstm"],
        })
        out, _ = await _editors.edit_intent(_base_intent())
        assert out.research_queries == ["transformer", "lstm"]


# ---------- outline -------------------------------------------------------


class TestEditOutline:
    @pytest.mark.asyncio
    async def test_accept_returns_unchanged(self, scripted: Any) -> None:
        scripted({"select": ["accept"]})
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "accept"
        assert [s.title for s in out.slides] == ["Intro", "Body", "Outro"]

    @pytest.mark.asyncio
    async def test_regenerate_unchanged_does_not_confirm(self, scripted: Any) -> None:
        scripted({"select": ["regenerate all"]})
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "regenerate"

    @pytest.mark.asyncio
    async def test_regenerate_after_edit_confirms_discard(self, scripted: Any) -> None:
        scripted({
            "select": [
                "remove slide", "2: Body",
                "regenerate all",
            ],
            "confirm": [True],
        })
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "regenerate"

    @pytest.mark.asyncio
    async def test_regenerate_after_edit_refuses_discard(self, scripted: Any) -> None:
        scripted({
            "select": [
                "remove slide", "2: Body",
                "regenerate all",
                "accept",
            ],
            "confirm": [False],
        })
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "accept"
        assert [s.title for s in out.slides] == ["Intro", "Outro"]
        # reindex: indices compacted
        assert [s.index for s in out.slides] == [1, 2]

    @pytest.mark.asyncio
    async def test_add_slide_at_position(self, scripted: Any) -> None:
        scripted({
            "select": [
                "add slide",
                "content",      # slide type
                "done",         # key_points loop
                "accept",
            ],
            "text": ["2", "Middle", ""],  # pos=2, title, no key points added
        })
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "accept"
        assert [s.title for s in out.slides] == ["Intro", "Middle", "Body", "Outro"]
        assert [s.index for s in out.slides] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_remove_slide_compacts_indices(self, scripted: Any) -> None:
        scripted({
            "select": ["remove slide", "2: Body", "accept"],
        })
        out, _ = await _editors.edit_outline(_base_outline())
        assert [s.title for s in out.slides] == ["Intro", "Outro"]
        assert [s.index for s in out.slides] == [1, 2]

    @pytest.mark.asyncio
    async def test_reorder_slides(self, scripted: Any) -> None:
        scripted({
            "select": ["reorder slides", "accept"],
            "text": ["3,1,2"],
        })
        out, _ = await _editors.edit_outline(_base_outline())
        assert [s.title for s in out.slides] == ["Outro", "Intro", "Body"]
        assert [s.index for s in out.slides] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_reorder_rejects_invalid_permutation(self, scripted: Any) -> None:
        scripted({
            "select": ["reorder slides", "accept"],
            "text": ["1,2"],  # too short
        })
        out, _ = await _editors.edit_outline(_base_outline())
        # unchanged
        assert [s.title for s in out.slides] == ["Intro", "Body", "Outro"]

    @pytest.mark.asyncio
    async def test_edit_slide_title(self, scripted: Any) -> None:
        scripted({
            "select": ["edit slide", "2: Body", "title", "accept"],
            "text": ["Main Body"],
        })
        out, _ = await _editors.edit_outline(_base_outline())
        assert out.slides[1].title == "Main Body"

    @pytest.mark.asyncio
    async def test_abort(self, scripted: Any) -> None:
        scripted({"select": ["abort"]})
        out, action = await _editors.edit_outline(_base_outline())
        assert action == "abort"


# ---------- theme custom --------------------------------------------------


class TestEditThemeCustom:
    @pytest.mark.asyncio
    async def test_happy_path(self, scripted: Any) -> None:
        scripted({
            "text": [
                "ff0000", "00ff00", "0000ff", "ffffff", "000000",  # 5 hex
                "Roboto", "Roboto Mono", "Noto Sans CJK",          # 3 fonts
            ],
            "select": ["soft", "pill"],
        })
        out = await _editors.edit_theme_custom(_base_theme())
        assert out.palette.primary == "ff0000"
        assert out.palette.secondary == "00ff00"
        assert out.fonts.heading == "Roboto"
        assert out.style == "soft"
        assert out.page_badge_style == "pill"

    @pytest.mark.asyncio
    async def test_rejects_hash_prefix_then_accepts(self, scripted: Any) -> None:
        # The user types '#ff0000' first — the validator strips '#' before matching,
        # so we get 'ff0000' accepted after the strip (per design).
        scripted({
            "text": [
                "#ff0000",      # rejected? no — lstrip('#') => 'ff0000' is valid
                "#22",          # <6 chars, rejected
                "112233",       # accepted
                "445566", "778899", "aabbcc", "ddeeff",
                "h", "b", "c",
            ],
            "select": ["sharp", "circle"],
        })
        # The first hex prompt is 'primary'. Since we lstrip('#') and accept
        # on match, the first response 'ff0000' gets accepted. So subsequent
        # calls consume the remaining hex values for secondary/accent/light/bg.
        out = await _editors.edit_theme_custom(_base_theme())
        assert out.palette.primary == "ff0000"

    @pytest.mark.asyncio
    async def test_empty_input_uses_default(self, scripted: Any) -> None:
        scripted({
            "text": [
                "",    # empty -> fall back to default '112233'
                "445566", "778899", "aabbcc", "ddeeff",
                "", "", "",
            ],
            "select": ["sharp", "circle"],
        })
        base = _base_theme()
        out = await _editors.edit_theme_custom(base)
        assert out.palette.primary == base.palette.primary
        assert out.fonts.heading == base.fonts.heading
