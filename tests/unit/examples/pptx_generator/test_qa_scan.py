"""Unit tests for examples/pptx_generator/wizard/_qa_scan.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from examples.pptx_generator.wizard import _qa_scan
from examples.pptx_generator.wizard._qa_scan import (
    DEFAULT_PATTERNS,
    QAMatch,
    _python_scan,
    _slide_index_for_line,
    scan_placeholders,
)


_SAMPLE_MD = """\
# Deck

## Slide 1
Welcome everyone.

## Slide 2
This page will be great.
Some lorem ipsum placeholder text here.

## Slide 3
Fully written content.
"""


_CLEAN_MD = """\
# Deck

## Slide 1
Content.

## Slide 2
Also content.
"""


class TestPythonScan:
    def test_finds_lorem_and_placeholder_and_this_page(self) -> None:
        matches = _python_scan(_SAMPLE_MD, list(DEFAULT_PATTERNS))
        patterns_found = {m.pattern for m in matches}
        assert "lorem" in patterns_found
        assert "placeholder" in patterns_found
        assert "this page" in patterns_found

    def test_slide_index_attribution(self) -> None:
        matches = _python_scan(_SAMPLE_MD, list(DEFAULT_PATTERNS))
        for m in matches:
            if m.pattern == "lorem":
                assert m.slide_index == 2
            if m.pattern == "this page":
                assert m.slide_index == 2

    def test_no_matches_in_clean_markdown(self) -> None:
        matches = _python_scan(_CLEAN_MD, list(DEFAULT_PATTERNS))
        assert matches == []


class TestSlideIndexForLine:
    def test_returns_none_before_first_marker(self) -> None:
        md = "Pre-amble line\n## Slide 1\n\n"
        assert _slide_index_for_line(md.splitlines(), 1) is None

    def test_walks_back_to_nearest_marker(self) -> None:
        md = "## Slide 4\nline\nline\nline\n"
        assert _slide_index_for_line(md.splitlines(), 4) == 4

    def test_html_comment_marker(self) -> None:
        md = "<!-- slide 7 -->\nsome line\n"
        assert _slide_index_for_line(md.splitlines(), 2) == 7


@pytest.mark.asyncio
class TestScanPlaceholders:
    async def test_no_md_text_returns_not_ran(self) -> None:
        report = await scan_placeholders(None)
        assert report.matches == []
        assert report.markitdown_ran is False
        assert report.rg_used is False

    async def test_falls_back_to_python_when_no_shell(self) -> None:
        report = await scan_placeholders(_SAMPLE_MD)
        assert report.markitdown_ran is True
        assert report.rg_used is False
        assert any(m.pattern == "lorem" for m in report.matches)

    async def test_returns_empty_on_clean_md(self) -> None:
        report = await scan_placeholders(_CLEAN_MD)
        assert report.matches == []

    async def test_uses_rg_when_available(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        md_file = tmp_path / "deck.md"
        md_file.write_text(_SAMPLE_MD, encoding="utf-8")

        monkeypatch.setattr(_qa_scan.shutil, "which", lambda name: "/fake/rg" if name == "rg" else None)

        class _ShellStub:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def invoke(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
                self.calls.append(args)
                return {
                    "exit_code": 0,
                    "stdout": f"{md_file}:7:Some lorem ipsum placeholder text here.\n",
                    "stderr": "",
                }

        shell = _ShellStub()
        report = await scan_placeholders(
            _SAMPLE_MD,
            md_path=str(md_file),
            shell_tool=shell,
        )
        assert report.rg_used is True
        assert len(report.matches) == 1
        assert report.matches[0].line_no == 7
        assert report.matches[0].slide_index == 2

    async def test_rg_exit_1_means_no_matches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        md_file = tmp_path / "deck.md"
        md_file.write_text(_CLEAN_MD, encoding="utf-8")
        monkeypatch.setattr(_qa_scan.shutil, "which", lambda name: "/fake/rg" if name == "rg" else None)

        class _ShellStub:
            async def invoke(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
                return {"exit_code": 1, "stdout": "", "stderr": ""}

        report = await scan_placeholders(
            _CLEAN_MD, md_path=str(md_file), shell_tool=_ShellStub(),
        )
        assert report.rg_used is True
        assert report.matches == []

    async def test_rg_error_falls_back_to_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        md_file = tmp_path / "deck.md"
        md_file.write_text(_SAMPLE_MD, encoding="utf-8")
        monkeypatch.setattr(_qa_scan.shutil, "which", lambda name: "/fake/rg" if name == "rg" else None)

        class _ShellStub:
            async def invoke(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
                return {"exit_code": 2, "stdout": "", "stderr": "oops"}

        report = await scan_placeholders(
            _SAMPLE_MD, md_path=str(md_file), shell_tool=_ShellStub(),
        )
        assert report.rg_used is False
        assert any(m.pattern == "lorem" for m in report.matches)

    async def test_rg_absent_uses_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        md_file = tmp_path / "deck.md"
        md_file.write_text(_SAMPLE_MD, encoding="utf-8")
        monkeypatch.setattr(_qa_scan.shutil, "which", lambda name: None)

        class _Shell:
            async def invoke(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
                raise AssertionError("shell.invoke should not be called when rg is absent")

        report = await scan_placeholders(
            _SAMPLE_MD, md_path=str(md_file), shell_tool=_Shell(),
        )
        assert report.rg_used is False
        assert any(m.pattern == "lorem" for m in report.matches)
