# examples/pptx_generator/app/slot_schemas.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CoverSlots(BaseModel):
    title: str
    subtitle: str | None = None
    author: str | None = None
    date: str | None = None


class AgendaItem(BaseModel):
    label: str
    sub: str | None = None


class AgendaSlots(BaseModel):
    title: str
    items: list[AgendaItem]


class BulletsBlock(BaseModel):
    kind: Literal["bullets"] = "bullets"
    items: list[str]


class TwoColumnBlock(BaseModel):
    kind: Literal["two_column"] = "two_column"
    left_items: list[str]
    right_items: list[str]


class CalloutBlock(BaseModel):
    kind: Literal["callout"] = "callout"
    text: str
    icon: str | None = None


ContentBlock = BulletsBlock | TwoColumnBlock | CalloutBlock


class ContentSlots(BaseModel):
    title: str
    body_blocks: list[ContentBlock]


class TransitionSlots(BaseModel):
    section_number: int
    section_title: str
    subtitle: str | None = None


class ClosingSlots(BaseModel):
    title: str
    call_to_action: str | None = None
    contact: str | None = None


SLOT_MODELS: dict[str, type[BaseModel]] = {
    "cover": CoverSlots,
    "agenda": AgendaSlots,
    "content": ContentSlots,
    "transition": TransitionSlots,
    "closing": ClosingSlots,
}
