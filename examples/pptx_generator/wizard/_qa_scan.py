"""QA placeholder scanner for stage 7 (compile-QA)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import Any

DEFAULT_PATTERNS: tuple[str, ...] = ("xxxx", "lorem", "placeholder", "this page")


@dataclass
class QAMatch:
    slide_index: int | None
    pattern: str
    line_text: str
    line_no: int


@dataclass
class QAReport:
    matches: list[QAMatch]
    markitdown_ran: bool
    rg_used: bool


_SLIDE_MARKER_RE = re.compile(
    r"^#+\s+Slide\s+(\d+)|^<!--\s*slide\s+(\d+)\s*-->",
    re.IGNORECASE,
)


def _slide_index_for_line(md_lines: list[str], line_no: int) -> int | None:
    """Walk backward from ``line_no`` (1-based) to find the most recent slide marker."""
    for i in range(line_no - 1, -1, -1):
        m = _SLIDE_MARKER_RE.match(md_lines[i])
        if m:
            raw = m.group(1) or m.group(2)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def _python_scan(md_text: str, patterns: list[str]) -> list[QAMatch]:
    lines = md_text.splitlines()
    lowered_patterns = [p.lower() for p in patterns]
    out: list[QAMatch] = []
    for line_no, line in enumerate(lines, start=1):
        lowered = line.lower()
        for pat, lowered_pat in zip(patterns, lowered_patterns):
            if lowered_pat in lowered:
                out.append(
                    QAMatch(
                        slide_index=_slide_index_for_line(lines, line_no),
                        pattern=pat,
                        line_text=line,
                        line_no=line_no,
                    )
                )
    return out


async def _rg_scan(shell_tool: Any, md_path: str, patterns: list[str]) -> list[QAMatch] | None:
    """Try `rg` first. Returns ``None`` if rg is missing or shell_tool rejects it."""
    if shutil.which("rg") is None:
        return None
    pattern_arg = "|".join(re.escape(p) for p in patterns)
    try:
        result = await shell_tool.invoke(
            {
                "command": ["rg", "-in", pattern_arg, md_path],
            },
            context=None,
        )
    except Exception:
        return None
    exit_code = result.get("exit_code") if isinstance(result, dict) else None
    if exit_code is None:
        return None
    # rg exits 1 when no matches; both 0 and 1 mean "ran successfully"
    if exit_code not in (0, 1):
        return None
    stdout = result.get("stdout", "") if isinstance(result, dict) else ""
    try:
        with open(md_path, encoding="utf-8") as fh:
            md_lines = fh.read().splitlines()
    except OSError:
        md_lines = []
    matches: list[QAMatch] = []
    for row in stdout.splitlines():
        # rg emits ``<path>:<line_no>:<content>``. On Windows the path
        # contains ``C:\\...``, so split from the right.
        parts = row.rsplit(":", maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            line_no = int(parts[1])
        except ValueError:
            continue
        line_text = parts[2]
        lowered = line_text.lower()
        pat = next(
            (p for p in patterns if p.lower() in lowered),
            patterns[0],
        )
        matches.append(
            QAMatch(
                slide_index=_slide_index_for_line(md_lines, line_no),
                pattern=pat,
                line_text=line_text,
                line_no=line_no,
            )
        )
    return matches


async def scan_placeholders(
    md_text: str | None,
    *,
    patterns: list[str] | None = None,
    md_path: str | None = None,
    shell_tool: Any = None,
    markitdown_ran: bool = True,
) -> QAReport:
    """Scan markdown for placeholder matches, preferring ``rg`` via ``shell_tool``.

    When ``md_text`` is ``None`` (e.g. markitdown wasn't run), returns an empty
    report with ``markitdown_ran=False``.
    """
    pats = list(patterns or DEFAULT_PATTERNS)
    if md_text is None:
        return QAReport(matches=[], markitdown_ran=False, rg_used=False)
    if shell_tool is not None and md_path is not None:
        via_rg = await _rg_scan(shell_tool, md_path, pats)
        if via_rg is not None:
            return QAReport(matches=via_rg, markitdown_ran=markitdown_ran, rg_used=True)
    matches = _python_scan(md_text, pats)
    return QAReport(matches=matches, markitdown_ran=markitdown_ran, rg_used=False)
