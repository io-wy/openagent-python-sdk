"""App-layer plugins for the pptx-agent example.

Each Pattern drives one stage of the 7-stage wizard. They inherit from
``openagents.interfaces.pattern.PatternPlugin`` so the runtime's
``setup()`` lifecycle populates ``self.context`` before ``execute()``
runs. Tests construct patterns directly and assign ``context`` manually
to bypass the full setup() dance.

This module starts with IntentAnalystPattern; sibling patterns
(ResearchPattern, OutlinePattern, ThemePattern, SlideGenPattern) are
appended in later tasks.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from openagents.interfaces.pattern import PatternPlugin

from ..state import (
    FontPairing,
    IntentReport,
    Palette,
    ResearchFindings,
    SlideIR,
    SlideOutline,
    ThemeCandidateList,
    ThemeSelection,
)

_INTENT_SYSTEM = """You are a presentation planning assistant.
Extract an IntentReport as JSON only. Required fields:
topic, audience, purpose(one of pitch|report|teaching|announcement|other),
tone(one of formal|casual|energetic|minimalist),
slide_count_hint(int 3..20), required_sections(list), visuals_hint(list),
research_queries(list of up to 5 concrete search queries),
language(zh|en|bilingual).
Output ONLY JSON without markdown fencing."""


_FENCE_RE = re.compile(r"^\s*```(?:\w+)?\n?(?P<body>.*?)\n?```", re.DOTALL)


def _extract_json_block(raw: str) -> str:
    """Return the substring most likely to be a JSON document.

    Strips leading/trailing markdown code fences. If no fence is present,
    trims whitespace. If a fence exists, ignores any text outside it.
    """
    text = (raw or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group("body").strip()
    return text


def _try_parse_intent(raw: str) -> tuple[IntentReport | None, str | None]:
    text = _extract_json_block(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc.msg}"
    try:
        return IntentReport.model_validate(data), None
    except ValidationError as exc:
        return None, f"Schema validation failed: {exc.errors()}"


class IntentAnalystPattern(PatternPlugin):
    """Stage 1: extract a validated IntentReport from the user's raw prompt.

    Budget: ``max_steps`` attempts. On validation failure the error is
    appended to the prompt before the next attempt. On final failure
    raises ``RuntimeError``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.max_steps = int((config or {}).get("max_steps", 3))

    async def execute(self) -> IntentReport:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("IntentAnalystPattern.context is not set; call setup() first")

        memory_view = getattr(ctx, "memory_view", {}) or {}
        goals = memory_view.get("user_goals", []) if isinstance(memory_view, dict) else []
        feedback = memory_view.get("user_feedback", []) if isinstance(memory_view, dict) else []
        priors = "\n".join(f"- {e.get('rule', '')}" for e in (goals + feedback))
        user_prompt = ctx.input_text or ""

        user_content = f"Known user preferences:\n{priors or '(none)'}\n\nUser request:\n{user_prompt}"

        last_raw = ""
        for step in range(1, self.max_steps + 1):
            messages = [
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": user_content},
            ]
            raw = await ctx.llm_client.complete(messages=messages)
            last_raw = str(raw or "")
            parsed, reason = _try_parse_intent(last_raw)
            if parsed is not None:
                ctx.state["intent"] = parsed.model_dump(mode="json")
                return parsed
            user_content = user_content + (
                f"\n\nPrevious attempt failed ({reason}). Raw output was:\n{last_raw}\nTry again with valid output."
            )
        raise RuntimeError(f"IntentAnalystPattern exhausted {self.max_steps} retries; last raw: {last_raw[:200]}")


# ---------------------------------------------------------------------------
# Stage 3: ResearchPattern
# ---------------------------------------------------------------------------

_RESEARCH_SYSTEM = """Given a set of search results per query, output a JSON
ResearchFindings with: queries_executed, sources (url/title/snippet),
key_facts (3..8 bullet-style facts), caveats. Output ONLY JSON, no markdown fencing."""


def _try_parse_research(raw: str) -> tuple[ResearchFindings | None, str | None]:
    text = _extract_json_block(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc.msg}"
    try:
        return ResearchFindings.model_validate(data), None
    except ValidationError as exc:
        return None, f"Schema validation failed: {exc.errors()}"


class ResearchPattern(PatternPlugin):
    """Stage 3: fetch search results via Tavily MCP (with REST fallback) then summarize.

    Pipeline:
      1. For each query in ``state['intent']['research_queries']`` (up to 5):
         try ``run_tool("tavily_mcp", {"query": q})``;
         on exception, try ``run_tool("tavily_fallback", {"query": q})``.
      2. Assemble a compact JSON payload of all queries + their results.
      3. Ask the LLM to synthesize a ResearchFindings JSON.
      4. On parse success, store result in ``state['research']`` and return it.
      5. If ``research_queries`` is empty, skip all steps and return empty findings.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.max_steps = int((config or {}).get("max_steps", 6))

    async def execute(self) -> ResearchFindings:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("ResearchPattern.context is not set")

        intent = ctx.state.get("intent") or {}
        queries = list(intent.get("research_queries") or [])[:5]
        if not queries:
            empty = ResearchFindings()
            ctx.state["research"] = empty.model_dump(mode="json")
            return empty

        search_blocks: list[dict[str, Any]] = []
        for q in queries:
            data = await self._search_one(q)
            search_blocks.append({"query": q, "results": data.get("results", [])})

        user_content = json.dumps({"queries": search_blocks}, ensure_ascii=False)
        last_raw = ""
        for _step in range(1, self.max_steps + 1):
            messages = [
                {"role": "system", "content": _RESEARCH_SYSTEM},
                {"role": "user", "content": user_content},
            ]
            raw = await ctx.llm_client.complete(messages=messages)
            last_raw = str(raw or "")
            parsed, reason = _try_parse_research(last_raw)
            if parsed is not None:
                ctx.state["research"] = parsed.model_dump(mode="json")
                return parsed
            user_content = user_content + (
                f"\n\nPrevious attempt failed ({reason}). Raw output was:\n{last_raw}\nRetry with valid JSON."
            )
        raise RuntimeError(f"ResearchPattern exhausted {self.max_steps} retries; last raw: {last_raw[:200]}")

    async def _search_one(self, query: str) -> dict[str, Any]:
        """MCP first, REST fallback on any exception.

        MCP tools expect ``{"tool": "<server-tool-name>", "arguments": {...}}``;
        the REST fallback takes the Tavily args directly.
        """
        try:
            result = await self.call_tool(
                "tavily_mcp",
                {"tool": "tavily-search", "arguments": {"query": query, "max_results": 5}},
            )
        except Exception:
            result = await self.call_tool("tavily_fallback", {"query": query})
        data = getattr(result, "data", result)
        if not isinstance(data, dict):
            return {"query": query, "results": []}
        return data


# ---------------------------------------------------------------------------
# Stage 4: OutlinePattern
# ---------------------------------------------------------------------------

_OUTLINE_SYSTEM = """Produce a SlideOutline JSON matching the SlideOutline pydantic schema.
Each slide must have: index (1..N, unique), type (cover|agenda|content|transition|closing|freeform),
title, key_points (may be empty), sources_cited (indexes into research.sources).
Output ONLY JSON; no markdown fencing."""


def _try_parse_outline(raw: str) -> tuple[SlideOutline | None, str | None]:
    text = _extract_json_block(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc.msg}"
    try:
        return SlideOutline.model_validate(data), None
    except ValidationError as exc:
        return None, f"Schema validation failed: {exc.errors()}"


class OutlinePattern(PatternPlugin):
    """Stage 4: produce a SlideOutline from intent + research.

    Retries ``max_steps`` times on JSON/schema failure. Stores result
    in ``state['outline']`` on success.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.max_steps = int((config or {}).get("max_steps", 3))

    async def execute(self) -> SlideOutline:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("OutlinePattern.context is not set")
        intent = ctx.state.get("intent") or {}
        research = ctx.state.get("research") or {}
        user_content = json.dumps({"intent": intent, "research": research}, ensure_ascii=False)

        last_raw = ""
        for _step in range(1, self.max_steps + 1):
            messages = [
                {"role": "system", "content": _OUTLINE_SYSTEM},
                {"role": "user", "content": user_content},
            ]
            raw = await ctx.llm_client.complete(messages=messages)
            last_raw = str(raw or "")
            parsed, reason = _try_parse_outline(last_raw)
            if parsed is not None:
                ctx.state["outline"] = parsed.model_dump(mode="json")
                return parsed
            user_content = user_content + (
                f"\n\nPrevious attempt failed ({reason}). Raw output was:\n{last_raw}\nRetry with valid JSON."
            )
        raise RuntimeError(f"OutlinePattern exhausted {self.max_steps} retries; last raw: {last_raw[:200]}")


# ---------------------------------------------------------------------------
# Stage 5: ThemePattern
# ---------------------------------------------------------------------------

from .catalog import FONT_PAIRINGS, PALETTES  # noqa: E402
from .slot_schemas import SLOT_MODELS  # noqa: E402

_THEME_SYSTEM = """Given an IntentReport and the catalogs of PALETTES and FONT_PAIRINGS
(each has a unique 'name'), propose a list of 3 to 5 distinct theme candidates
that each fit the tone/language. Each candidate picks one palette_name and one
font_pairing_name from the catalogs, plus a style (sharp|soft|rounded|pill) and
page_badge_style (circle|pill). Return JSON of the form
{"candidates": [{"palette_name": ..., "font_pairing_name": ..., "style": ..., "page_badge_style": ...}, ...]}
with 3 to 5 entries. No markdown fencing."""


def _try_parse_json_dict(raw: str) -> dict[str, Any] | None:
    text = _extract_json_block(raw)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class ThemePattern(PatternPlugin):
    """Stage 5: propose 3-5 theme candidates drawn from the built-in catalog."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.max_steps = int((config or {}).get("max_steps", 2))

    async def execute(self) -> ThemeCandidateList:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("ThemePattern.context is not set")

        intent = ctx.state.get("intent") or {}
        decisions = ctx.memory_view.get("decisions", []) if hasattr(ctx, "memory_view") else []
        user_content = json.dumps(
            {
                "intent": intent,
                "palette_catalog": PALETTES,
                "font_catalog": FONT_PAIRINGS,
                "prior_decisions": [e.get("rule") for e in decisions],
            },
            ensure_ascii=False,
        )

        last_raw = ""
        for _step in range(1, self.max_steps + 1):
            messages = [
                {"role": "system", "content": _THEME_SYSTEM},
                {"role": "user", "content": user_content},
            ]
            raw = await ctx.llm_client.complete(messages=messages)
            last_raw = str(raw or "")
            parsed = _try_parse_json_dict(last_raw)
            entries = parsed.get("candidates") if parsed else None
            if not isinstance(entries, list):
                user_content = user_content + (f"\n\nPrevious output lacked a 'candidates' list:\n{last_raw}\nRetry.")
                continue
            candidates: list[ThemeSelection] = []
            failures: list[str] = []
            for choice in entries:
                if not isinstance(choice, dict):
                    failures.append("entry is not an object")
                    continue
                pal = next((p for p in PALETTES if p["name"] == choice.get("palette_name")), None)
                font = next((f for f in FONT_PAIRINGS if f["name"] == choice.get("font_pairing_name")), None)
                if not pal or not font:
                    failures.append(f"unknown palette/font in {choice!r}")
                    continue
                try:
                    candidates.append(
                        ThemeSelection(
                            palette=Palette(**pal["palette"]),
                            fonts=FontPairing(heading=font["heading"], body=font["body"], cjk=font["cjk"]),
                            style=choice.get("style", "soft"),
                            page_badge_style=choice.get("page_badge_style", "circle"),
                        )
                    )
                except ValidationError as exc:
                    failures.append(str(exc))
            try:
                bundle = ThemeCandidateList(candidates=candidates)
            except ValidationError as exc:
                user_content = user_content + (
                    f"\n\nCandidate bundle invalid ({exc}). Previous attempt yielded "
                    f"{len(candidates)} candidates with failures: {failures}. "
                    f"Valid palette names: {[p['name'] for p in PALETTES]}; "
                    f"valid font pairing names: {[f['name'] for f in FONT_PAIRINGS]}. "
                    "Return 3 to 5 candidates."
                )
                continue
            ctx.state["theme_candidates"] = bundle.model_dump(mode="json")
            return bundle
        raise RuntimeError(f"ThemePattern exhausted {self.max_steps} retries; last raw: {last_raw[:200]}")


# ---------------------------------------------------------------------------
# Stage 6: SlideGenPattern
# ---------------------------------------------------------------------------

_SLIDEGEN_SYSTEM_TMPL = """You are filling a slide template of type {slide_type}.
Return a JSON object matching the {slide_type} slot schema exactly.
No markdown fencing, JSON only."""


class SlideGenPattern(PatternPlugin):
    """Stage 6: generate one SlideIR from a spec + theme.

    Per-slide payload comes in via ``ctx.input_text`` as JSON:
    ``{"target_spec": {...}, "theme": {...}}``.

    Strategy:
      1. Look up the slot schema for ``spec.type``.
      2. If schema exists: LLM fills slots, we validate, retry on failure.
      3. After ``max_retries`` schema failures: fall back to ``freeform``
         if ``allow_freeform_fallback`` is True, else raise.
      4. If spec.type is unknown: fall back to ``freeform`` directly.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.max_retries = int((config or {}).get("max_retries", 2))
        self.allow_freeform = bool((config or {}).get("allow_freeform_fallback", True))

    async def execute(self) -> SlideIR:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("SlideGenPattern.context is not set")

        try:
            payload = json.loads(ctx.input_text or "{}")
        except json.JSONDecodeError:
            payload = {}
        spec = payload.get("target_spec") or {}
        theme = payload.get("theme") or {}
        slide_type = spec.get("type", "content")
        model = SLOT_MODELS.get(slide_type)
        if model is None:
            return self._freeform(spec, reason=f"unknown type {slide_type}")

        system = _SLIDEGEN_SYSTEM_TMPL.format(slide_type=slide_type)
        user_content = json.dumps({"spec": spec, "theme": theme}, ensure_ascii=False)
        last_raw = ""

        for attempt in range(self.max_retries + 1):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]
            raw = await ctx.llm_client.complete(messages=messages)
            last_raw = str(raw or "")
            choice = _try_parse_json_dict(last_raw)
            if choice is None:
                user_content = user_content + f"\n\nPrevious output not JSON:\n{last_raw}\nRetry."
                continue
            try:
                slots_model = model.model_validate(choice)
            except ValidationError as exc:
                user_content = user_content + f"\n\nSchema invalid: {exc.errors()}\nRetry."
                continue
            slide = SlideIR(
                index=int(spec.get("index", 1)),
                type=slide_type,  # type: ignore[arg-type]
                slots=slots_model.model_dump(),
                generated_at=datetime.now(timezone.utc),
            )
            return slide

        if not self.allow_freeform:
            raise RuntimeError(
                f"SlideGenPattern failed for slide {spec.get('index')} after {self.max_retries + 1} tries; "
                f"last raw: {last_raw[:200]}"
            )
        return self._freeform(spec, reason=f"schema-retry-exhausted: {last_raw[:80]}")

    def _freeform(self, spec: dict[str, Any], *, reason: str) -> SlideIR:
        placeholder_js = (
            f"// FREEFORM fallback for slide index={spec.get('index')} reason={reason!r}\n"
            "function createSlide(pres, theme) {\n"
            "  const slide = pres.addSlide();\n"
            "  slide.background = { color: theme.bg };\n"
            f"  slide.addText({json.dumps(spec.get('title', 'Untitled'))}, {{ x: 0.5, y: 2.4, w: 9, h: 0.8, "
            "fontSize: 32, fontFace: 'Arial', color: theme.primary, bold: true, align: 'center' }});\n"
            "  return slide;\n"
            "}\n"
            "module.exports = { createSlide };\n"
        )
        return SlideIR(
            index=int(spec.get("index", 1)),
            type="freeform",
            slots={},
            freeform_js=placeholder_js,
            generated_at=datetime.now(timezone.utc),
        )
