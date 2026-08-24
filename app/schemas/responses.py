from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import APIModel, PaginationMeta, SuccessResponse


class EventOut(APIModel):
    id: str
    title: str
    date_iso: str
    date_jalali: str
    repeat: Literal["none", "daily", "weekly", "monthly", "yearly"] = "none"
    notify_status: str = "pending"
    tz_name: str = "UTC"
    category: str = "general"
    pinned: bool = False
    note: str = ""
    reminder_hour: int = 9
    repeat_until: str | None = None


class ListEventsResponse(SuccessResponse):
    targets: list[EventOut] = Field(default_factory=list)
    has_more: bool = False
    meta: PaginationMeta


class EventMutationResponse(SuccessResponse):
    pass


class NoteResponse(SuccessResponse):
    note: str


class PinResponse(SuccessResponse):
    pinned: bool