from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from openagents.utils.env_doctor import EnvironmentReport

HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class IntentReport(BaseModel):
    topic: str
    audience: str
    purpose: Literal["pitch", "report", "teaching", "announcement", "other"]
    tone: Literal["formal", "casual", "energetic", "minimalist"]
    slide_count_hint: int = Field(ge=3, le=20)
    required_sections: list[str]
    visuals_hint: list[str]
    research_queries: list[str]
    language: Literal["zh", "en", "bilingual"]


class Source(BaseModel):
    url: str
    title: str
    snippet: str
    published_at: str | None = None
    score: float | None = None


class ResearchFindings(BaseModel):
    queries_executed: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SlideSpec(BaseModel):
    index: int = Field(ge=1)
    type: Literal["cover", "agenda", "content", "transition", "closing", "freeform"]
    title: str
    key_points: list[str] = Field(default_factory=list)
    sources_cited: list[int] = Field(default_factory=list)


class SlideOutline(BaseModel):
    slides: list[SlideSpec]

    @model_validator(mode="after")
    def _unique_indexes(self) -> "SlideOutline":
        indexes = [s.index for s in self.slides]
        if len(set(indexes)) != len(indexes):
            raise ValueError("slide indexes must be unique")
        return self


class Palette(BaseModel):
    primary: str
    secondary: str
    accent: str
    light: str
    bg: str

    @field_validator("primary", "secondary", "accent", "light", "bg")
    @classmethod
    def validate_hex(cls, v: str) -> str:
        if not HEX_RE.match(v):
            raise ValueError("palette colors must be 6-digit hex without '#'")
        return v.lower()


class FontPairing(BaseModel):
    heading: str
    body: str
    cjk: str


class ThemeSelection(BaseModel):
    palette: Palette
    fonts: FontPairing
    style: Literal["sharp", "soft", "rounded", "pill"]
    page_badge_style: Literal["circle", "pill"]


class SlideIR(BaseModel):
    index: int = Field(ge=1)
    type: Literal["cover", "agenda", "content", "transition", "closing", "freeform"]
    slots: dict[str, Any]
    freeform_js: str | None = None
    generated_at: AwareDatetime

    @model_validator(mode="after")
    def _freeform_requires_js(self) -> "SlideIR":
        if self.type == "freeform" and not self.freeform_js:
            raise ValueError("freeform SlideIR requires freeform_js")
        return self


class DeckProject(BaseModel):
    slug: str
    created_at: AwareDatetime
    stage: Literal[
        "intent", "env", "research", "outline",
        "theme", "slides", "compile", "done"
    ]
    intent: IntentReport | None = None
    research: ResearchFindings | None = None
    outline: SlideOutline | None = None
    theme: ThemeSelection | None = None
    slides: list[SlideIR] = Field(default_factory=list)
    env_report: EnvironmentReport | None = None
    last_error: str | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase alnum/underscore/dash, start with alnum, ≤64 chars")
        return v
