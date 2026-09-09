from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import APIModel, PaginationMeta, ReminderSpec, SuccessResponse


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
    # date_iso is where the series started — for a birthday, the birth date.
    # next_date_iso is the occurrence being counted down to. They differ only
    # for recurring events, and conflating them is what made a yearly birthday
    # sort correctly while displaying "6 years ago".
    next_date_iso: str = ""
    next_date_jalali: str = ""
    all_day: bool = True
    time_hm: str | None = None
    reminders: list[ReminderSpec] = Field(default_factory=list)
    reminder_hour: int = 9
    reminder_minute: int = 0
    repeat_until: str | None = None
    lead_repeat: str = "none"
    # None for an ordinary event, "owner" or "member" for a shared one.
    share_role: str | None = None


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
