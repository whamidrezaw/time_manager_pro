from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import (
    MAX_REMINDERS_PER_EVENT,
    EventIdPayload,
    InitDataPayload,
    ReminderSpec,
)

RepeatType = Literal["none", "daily", "weekly", "monthly", "yearly"]
CategoryType = Literal[
    "general",
    "birthday",
    "work",
    "family",
    "health",
    "travel",
    "finance",
    "study",
    "other",
]


ListFilterType = Literal[
    "all",
    "pinned",
    "past",
    "general",
    "birthday",
    "work",
    "family",
    "health",
    "travel",
    "finance",
    "study",
    "other",
]


class ListEventsRequest(InitDataPayload):
    skip: int = Field(default=0, ge=0, le=5000)
    # Searching and filtering moved to the server: doing it in the browser only
    # ever saw the 50 events of the current page, so a match on page 3 looked
    # like no match at all.
    q: str = Field(default="", max_length=100)
    filter: ListFilterType = "all"


class EventBaseRequest(InitDataPayload):
    title: str = Field(..., min_length=1, max_length=200)
    date: str = Field(..., min_length=10, max_length=10)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    repeat: RepeatType = "none"
    repeat_until: str | None = Field(default=None)
    # Deliberately separate from `repeat`: one answers "does this happen
    # again", the other "how often should I hear about it until it does".
    lead_repeat: Literal["none", "daily", "weekly", "monthly"] = "none"
    category: CategoryType = "general"
    note: str = Field(default="", max_length=2000)
    pinned: bool = False
    # Sent by the client, exactly like `timezone` above. The worker has no
    # initData when it fires a reminder, so the language has to live on the
    # document by the time it is needed.
    lang: Literal["en", "fa"] = "en"
    all_day: bool = True
    time_hm: str | None = Field(default=None, max_length=5)
    reminders: list[ReminderSpec] = Field(
        default_factory=list,
        max_length=MAX_REMINDERS_PER_EVENT,
    )
    # Kept so that a client which has not been updated yet still schedules
    # correctly: an empty `reminders` list falls back to this pair.
    reminder_hour: int = Field(default=9, ge=0, le=23)
    reminder_minute: int = Field(default=0, ge=0, le=59)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, value: str) -> str:
        value = value.strip()
        # تاریخ واقعی را چک می‌کنه — "2026-13-45" رد می‌شه
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date must be a valid date in YYYY-MM-DD format")

        # محدودیت سال معقول
        if not (1900 <= parsed.year <= 2200):
            raise ValueError("year must be between 1900 and 2200")

        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        return value or "UTC"

    @field_validator("repeat_until")
    @classmethod
    def validate_repeat_until_format(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("repeat_until must be a valid date in YYYY-MM-DD format")
        if not (1900 <= parsed.year <= 2200):
            raise ValueError("repeat_until year must be between 1900 and 2200")
        return value

    @field_validator("time_hm")
    @classmethod
    def validate_time_hm(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("time_hm must be a valid time in HH:MM format") from None
        return parsed.strftime("%H:%M")

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class AddEventRequest(EventBaseRequest):
    pass


class EditEventRequest(EventBaseRequest):
    event_id: str = Field(..., min_length=1, max_length=64)


class DeleteEventRequest(EventIdPayload):
    pass


class SaveNoteRequest(EventIdPayload):
    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class PinEventRequest(EventIdPayload):
    pinned: bool = False
