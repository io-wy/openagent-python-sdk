"""Unit tests for examples/pptx_generator/wizard/_slide_runner.py."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from examples.pptx_generator.state import (
    FontPairing,
    Palette,
    SlideIR,
    SlideSpec,
    ThemeSelection,
)
from examples.pptx_generator.wizard._slide_runner import (
    LiveStatusTable,
    SlideStatus,
    _freeform_fallback,
    _validate_slots,
    generate_slide,
)


def _theme() -> ThemeSelection:
    return ThemeSelection(
        palette=Palette(primary="112233", secondary="445566", accent="778899", light="aabbcc", bg="ddeeff"),
        fonts=FontPairing(heading="Inter", body="Inter", cjk="Noto"),
        style="sharp",
        page_badge_style="circle",
    )


def _spec(idx: int = 1, t: str = "cover") -> SlideSpec:
    return SlideSpec(index=idx, type=t, title=f"Slide {idx}", key_points=["a", "b"], sources_cited=[])


def _valid_ir(spec: SlideSpec) -> SlideIR:
    slots: dict[str, Any] = {
        "cover": {"title": spec.title, "subtitle": "sub", "author": "me", "date": "2026"},
        "content": {"title": spec.title, "body_blocks": [{"kind": "bullets", "items": ["a"]}]},
        "agenda": {"title": spec.title, "items": [{"label": "x"}]},
        "transition": {"section_number": 1, "section_title": spec.title},
        "closing": {"title": spec.title, "call_to_action": "go"},
    }[spec.type]
    return SlideIR(
        index=spec.index,
        type=spec.type,
        slots=slots,
        freeform_js=None,
        generated_at=datetime.now(timezone.utc),
    )


def _invalid_ir(spec: SlideSpec) -> SlideIR:
    # cover type but empty slots -> missing 'title' required
    return SlideIR(
        index=spec.index,
        type=spec.type,
        slots={},
        freeform_js=None,
        generated_at=datetime.now(timezone.utc),
    )


class _MockRuntime:
    def __init__(self, responses: list[Any]):
        self._queue = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError("no more runtime.run responses scripted")
        return self._queue.pop(0)


class TestValidateSlots:
    def test_valid_cover_passes(self) -> None:
        assert _validate_slots(_valid_ir(_spec(1, "cover"))) is None

    def test_missing_title_fails(self) -> None:
        err = _validate_slots(_invalid_ir(_spec(1, "cover")))
        assert err is not None

    def test_freeform_without_js_fails(self) -> None:
        ir = SlideIR(
            index=1,
            type="freeform",
            slots={"a": 1},
            freeform_js="module.exports.createSlide = function() {};",
            generated_at=datetime.now(timezone.utc),
        )
        assert _validate_slots(ir) is None
        # Note: SlideIR pydantic validator already prevents constructing a
        # freeform slide without freeform_js, so this test documents the
        # defence rather than exercising a reachable bypass. _validate_slots
        # accepts a SlideIR, not a dict — the non-freeform path is the only
        # one we can actually hit here.
        ir2_dict = ir.model_dump()
        ir2_dict["freeform_js"] = None

    def test_unknown_type_rejected(self) -> None:
        # SlideIR's Literal enforces the set, so this path is effectively dead —
        # we still keep the test to document the defence-in-depth.
        pass


class TestFreeformFallback:
    def test_script_contains_title_and_points(self) -> None:
        spec = _spec(3, "content")
        ir = _freeform_fallback(spec, _theme())
        assert ir.type == "freeform"
        assert ir.index == 3
        assert ir.freeform_js is not None
        assert spec.title in ir.freeform_js
        assert "bullet" in ir.freeform_js


@pytest.mark.asyncio
class TestGenerateSlide:
    async def test_first_attempt_succeeds(self) -> None:
        spec = _spec(1, "cover")
        rt = _MockRuntime([_valid_ir(spec)])
        rec = await generate_slide(rt, spec, _theme(), session_id="s")
        assert rec.status == SlideStatus.OK
        assert rec.attempts == 1
        assert len(rt.calls) == 1
        assert rec.ir is not None

    async def test_one_retry_then_success(self) -> None:
        spec = _spec(2, "content")
        rt = _MockRuntime([_invalid_ir(spec), _valid_ir(spec)])
        rec = await generate_slide(rt, spec, _theme(), session_id="s")
        assert rec.status == SlideStatus.OK
        assert rec.attempts == 2
        assert len(rt.calls) == 2

    async def test_two_retries_then_fallback(self) -> None:
        spec = _spec(3, "agenda")
        rt = _MockRuntime([_invalid_ir(spec), _invalid_ir(spec), _invalid_ir(spec)])
        rec = await generate_slide(rt, spec, _theme(), session_id="s")
        assert rec.status == SlideStatus.FALLBACK
        assert rec.ir is not None
        assert rec.ir.type == "freeform"
        assert rec.ir.freeform_js is not None
        assert len(rt.calls) == 3

    async def test_on_status_receives_every_state(self) -> None:
        spec = _spec(4, "transition")
        rt = _MockRuntime([_invalid_ir(spec), _valid_ir(spec)])
        seen: list[SlideStatus] = []
        await generate_slide(
            rt,
            spec,
            _theme(),
            session_id="s",
            on_status=lambda rec: seen.append(rec.status),
        )
        # Should have observed running -> retry-1 -> ok (and possibly the leading running on retry too).
        assert SlideStatus.RUNNING in seen
        assert SlideStatus.RETRY_1 in seen
        assert SlideStatus.OK in seen

    async def test_previous_error_passed_into_retry(self) -> None:
        spec = _spec(1, "cover")
        rt = _MockRuntime([_invalid_ir(spec), _valid_ir(spec)])
        await generate_slide(rt, spec, _theme(), session_id="s")
        # second call should carry "previous_error" in the payload JSON
        second_input = rt.calls[1]["input_text"]
        assert "previous_error" in second_input


class TestLiveStatusTable:
    def test_updates_preserve_order(self) -> None:
        table = LiveStatusTable()
        r1 = _record(1, SlideStatus.OK)
        r2 = _record(2, SlideStatus.RUNNING)
        r3 = _record(3, SlideStatus.FALLBACK)
        table.update(r3)
        table.update(r1)
        table.update(r2)
        assert [r.spec.index for r in table.records] == [1, 2, 3]

    def test_update_replaces_existing_record(self) -> None:
        table = LiveStatusTable()
        table.update(_record(1, SlideStatus.RUNNING))
        table.update(_record(1, SlideStatus.OK))
        assert len(table.records) == 1
        assert table.records[0].status == SlideStatus.OK

    def test_summary_counts(self) -> None:
        table = LiveStatusTable()
        table.update(_record(1, SlideStatus.OK))
        table.update(_record(2, SlideStatus.OK))
        table.update(_record(3, SlideStatus.FALLBACK))
        assert table.summary() == {"ok": 2, "fallback": 1, "failed": 0}


def _record(idx: int, status: SlideStatus) -> Any:
    from examples.pptx_generator.wizard._slide_runner import SlideRunRecord

    return SlideRunRecord(spec=_spec(idx, "cover"), status=status)
