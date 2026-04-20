"""Reusable interactive editors for intent / outline / theme (custom)."""

from __future__ import annotations

import re
from typing import Literal

from openagents.cli.wizard import Wizard

from ..state import (
    FontPairing,
    IntentReport,
    Palette,
    SlideOutline,
    SlideSpec,
    ThemeSelection,
)

HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

_PURPOSES = ["pitch", "report", "teaching", "announcement", "other"]
_TONES = ["formal", "casual", "energetic", "minimalist"]
_LANGUAGES = ["zh", "en", "bilingual"]
_SLIDE_TYPES = ["cover", "agenda", "content", "transition", "closing", "freeform"]
_STYLES = ["sharp", "soft", "rounded", "pill"]
_BADGES = ["circle", "pill"]


# ---------- intent --------------------------------------------------------


EditIntentAction = Literal["confirm", "edit field", "regenerate", "abort"]


async def edit_intent(report: IntentReport) -> tuple[IntentReport, EditIntentAction]:
    """Drive the intent two-level edit loop.

    Returns ``(updated_report, final_action)``. ``final_action`` is one of
    ``confirm`` / ``regenerate`` / ``abort``.
    """
    current = report
    while True:
        action = await Wizard.select(
            "Intent action?",
            choices=["confirm", "edit field", "regenerate", "abort"],
            default="confirm",
        )
        if action in ("confirm", "regenerate", "abort"):
            return current, action  # type: ignore[return-value]
        current = await _edit_intent_field(current)


async def _edit_intent_field(report: IntentReport) -> IntentReport:
    field = await Wizard.select(
        "Which field?",
        choices=[
            "topic",
            "audience",
            "purpose",
            "tone",
            "slide_count_hint",
            "language",
            "required_sections",
            "visuals_hint",
            "research_queries",
        ],
    )
    data = report.model_dump()
    if field == "topic":
        data["topic"] = await Wizard.text("Topic", default=report.topic) or report.topic
    elif field == "audience":
        data["audience"] = await Wizard.text("Audience", default=report.audience) or report.audience
    elif field == "purpose":
        data["purpose"] = await Wizard.select("Purpose", choices=_PURPOSES, default=report.purpose)
    elif field == "tone":
        data["tone"] = await Wizard.select("Tone", choices=_TONES, default=report.tone)
    elif field == "slide_count_hint":
        while True:
            raw = await Wizard.text("Slide count (3-20)", default=str(report.slide_count_hint))
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if 3 <= value <= 20:
                data["slide_count_hint"] = value
                break
    elif field == "language":
        data["language"] = await Wizard.select("Language", choices=_LANGUAGES, default=report.language)
    else:
        data[field] = await _edit_string_list(report.__getattribute__(field), prompt_name=field)
    return IntentReport.model_validate(data)


async def _edit_string_list(items: list[str], *, prompt_name: str) -> list[str]:
    current = list(items)
    while True:
        action = await Wizard.select(
            f"{prompt_name} actions",
            choices=["done", "add", "remove", "reorder", "edit-item"],
            default="done",
        )
        if action == "done":
            return current
        if action == "add":
            value = await Wizard.text(f"New {prompt_name} item", default="")
            if value:
                current.append(value)
        elif action == "remove":
            if not current:
                continue
            label = await Wizard.select("Remove which?", choices=current)
            current.remove(label)
        elif action == "reorder":
            if len(current) < 2:
                continue
            raw = await Wizard.text(
                f"New order as comma-separated 1-based indices (e.g. 2,1,3) over {len(current)}",
                default=",".join(str(i + 1) for i in range(len(current))),
            )
            try:
                order = [int(x.strip()) for x in raw.split(",") if x.strip()]
                if sorted(order) == list(range(1, len(current) + 1)):
                    current = [current[i - 1] for i in order]
            except ValueError:
                continue
        elif action == "edit-item":
            if not current:
                continue
            label = await Wizard.select("Edit which?", choices=current)
            new_value = await Wizard.text("New value", default=label)
            idx = current.index(label)
            if new_value:
                current[idx] = new_value


# ---------- outline -------------------------------------------------------


EditOutlineAction = Literal["accept", "regenerate", "abort"]


async def edit_outline(outline: SlideOutline) -> tuple[SlideOutline, EditOutlineAction]:
    """Drive the outline CRUD+reorder loop.

    Returns ``(updated_outline, final_action)`` where ``final_action`` is
    ``accept`` / ``regenerate`` / ``abort``.
    """
    slides = list(outline.slides)
    while True:
        action = await Wizard.select(
            "Outline action?",
            choices=[
                "accept",
                "add slide",
                "remove slide",
                "reorder slides",
                "edit slide",
                "regenerate all",
                "abort",
            ],
            default="accept",
        )
        if action == "accept":
            return SlideOutline(slides=slides), "accept"
        if action == "abort":
            return SlideOutline(slides=slides), "abort"
        if action == "regenerate all":
            if slides != list(outline.slides):
                ok = await Wizard.confirm(
                    "Regenerate discards your local edits. Continue?",
                    default=False,
                )
                if not ok:
                    continue
            return outline, "regenerate"
        if action == "add slide":
            slides = await _outline_add(slides)
        elif action == "remove slide":
            slides = _outline_remove(slides, await _pick_index(slides, "Remove which slide?"))
        elif action == "reorder slides":
            slides = await _outline_reorder(slides)
        elif action == "edit slide":
            idx = await _pick_index(slides, "Edit which slide?")
            slides = await _outline_edit_one(slides, idx)


async def _outline_add(slides: list[SlideSpec]) -> list[SlideSpec]:
    pos_raw = await Wizard.text(f"Insert position (1-{len(slides) + 1})", default=str(len(slides) + 1))
    try:
        pos = max(1, min(len(slides) + 1, int(pos_raw)))
    except (TypeError, ValueError):
        return slides
    slide_type = await Wizard.select("Slide type", choices=_SLIDE_TYPES, default="content")
    title = await Wizard.text("Title", default="")
    key_points = await _edit_string_list([], prompt_name="key_points")
    new_slide = SlideSpec(index=pos, type=slide_type, title=title or "Untitled", key_points=key_points)
    slides = slides[: pos - 1] + [new_slide] + slides[pos - 1 :]
    return _reindex(slides)


def _outline_remove(slides: list[SlideSpec], idx: int | None) -> list[SlideSpec]:
    if idx is None:
        return slides
    slides = [s for s in slides if s.index != idx]
    return _reindex(slides)


async def _outline_reorder(slides: list[SlideSpec]) -> list[SlideSpec]:
    if len(slides) < 2:
        return slides
    raw = await Wizard.text(
        f"New order as comma-separated 1-based indices over {len(slides)}",
        default=",".join(str(i + 1) for i in range(len(slides))),
    )
    try:
        order = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return slides
    if sorted(order) != list(range(1, len(slides) + 1)):
        return slides
    reordered = [slides[i - 1] for i in order]
    return _reindex(reordered)


async def _outline_edit_one(slides: list[SlideSpec], idx: int | None) -> list[SlideSpec]:
    if idx is None:
        return slides
    target = next((s for s in slides if s.index == idx), None)
    if target is None:
        return slides
    field = await Wizard.select(
        "Which field?",
        choices=["type", "title", "key_points", "sources_cited", "done"],
        default="done",
    )
    if field == "done":
        return slides
    data = target.model_dump()
    if field == "type":
        data["type"] = await Wizard.select("Type", choices=_SLIDE_TYPES, default=target.type)
    elif field == "title":
        data["title"] = await Wizard.text("Title", default=target.title) or target.title
    elif field == "key_points":
        data["key_points"] = await _edit_string_list(list(target.key_points), prompt_name="key_points")
    elif field == "sources_cited":
        data["sources_cited"] = await _edit_int_list(list(target.sources_cited))
    updated = SlideSpec.model_validate(data)
    replaced = [updated if s.index == idx else s for s in slides]
    return _reindex(replaced)


async def _edit_int_list(items: list[int]) -> list[int]:
    raw = await Wizard.text(
        "Comma-separated source indices (0-based)",
        default=",".join(str(i) for i in items),
    )
    out: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            return items
    return out


async def _pick_index(slides: list[SlideSpec], prompt: str) -> int | None:
    if not slides:
        return None
    labels = [f"{s.index}: {s.title}" for s in slides]
    label = await Wizard.select(prompt, choices=labels)
    try:
        return int(label.split(":", maxsplit=1)[0])
    except (ValueError, AttributeError):
        return None


def _reindex(slides: list[SlideSpec]) -> list[SlideSpec]:
    out: list[SlideSpec] = []
    for i, s in enumerate(slides, start=1):
        if s.index != i:
            out.append(s.model_copy(update={"index": i}))
        else:
            out.append(s)
    return out


# ---------- theme (custom editor) -----------------------------------------


async def edit_theme_custom(base: ThemeSelection) -> ThemeSelection:
    """Walk the full theme editor, seeded with ``base``."""
    palette_fields = ["primary", "secondary", "accent", "light", "bg"]
    palette_data = base.palette.model_dump()
    for field in palette_fields:
        palette_data[field] = await _prompt_hex(field, palette_data[field])
    font_fields = ["heading", "body", "cjk"]
    font_data = base.fonts.model_dump()
    for field in font_fields:
        font_data[field] = await Wizard.text(f"font {field}", default=font_data[field]) or font_data[field]
    style = await Wizard.select("Style", choices=_STYLES, default=base.style)
    badge = await Wizard.select("Badge style", choices=_BADGES, default=base.page_badge_style)
    return ThemeSelection(
        palette=Palette.model_validate(palette_data),
        fonts=FontPairing.model_validate(font_data),
        style=style,
        page_badge_style=badge,
    )


async def _prompt_hex(name: str, default: str) -> str:
    while True:
        raw = await Wizard.text(f"{name} hex (6 chars, no '#')", default=default)
        candidate = (raw or default).lstrip("#")
        if HEX_RE.match(candidate):
            return candidate.lower()
