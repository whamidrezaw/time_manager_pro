from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VALID_REPEAT_VALUES = ("none", "daily", "weekly", "monthly", "yearly")
VALID_CATEGORY_VALUES = (
    "general",
    "birthday",
    "work",
    "family",
    "health",
    "travel",
    "finance",
    "study",
    "other",
)


class APIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


MAX_REMINDERS_PER_EVENT = 3


class ReminderSpec(APIModel):
    """One reminder on an event.

    mode="absolute" fires at hour:minute on the day of the event, which is the
    only thing that makes sense for an all-day event. mode="relative" fires
    offset_minutes before a timed event starts.

    The UI currently exposes one reminder per event; the list shape is here so
    that raising that later needs no schema migration.
    """

    mode: Literal["absolute", "relative"] = "absolute"
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    offset_minutes: int = Field(default=0, ge=0, le=43200)


class InitDataPayload(APIModel):
    initData: str = Field(..., min_length=1)


class EventIdPayload(InitDataPayload):
    event_id: str = Field(..., min_length=1, max_length=64)


class SuccessResponse(APIModel):
    success: bool = True


class ErrorResponse(APIModel):
    success: bool = False
    error: str


class HealthResponse(APIModel):
    status: str
    db: str
    ts: datetime


class PaginationMeta(APIModel):
    has_more: bool
    returned: int
    skip: int


class MessageResponse(SuccessResponse):
    message: str


class GenericDataResponse(SuccessResponse):
    data: dict[str, Any]
