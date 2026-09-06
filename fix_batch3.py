#!/usr/bin/env python3
"""
fix_batch3.py — TimeManager Pro, batch 3a: times on events (backend only).

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch3.py
    ruff check . ; pytest -q

What changes

  Events gain `all_day` and `time_hm`, so "meeting at 14:30" becomes something
  the model can express, instead of a bare date with a separate reminder hour.

  Reminders become a list of specs. An absolute reminder fires at a wall-clock
  time on the day of the event, which is the only sensible option for an
  all-day event. A relative one fires a fixed number of minutes before a timed
  event starts. The Mini App still shows a single reminder; the list shape is
  stored now so that raising that later costs no migration.

  Two real bugs go along with it. A monthly event on the 31st no longer drifts
  to the 28th for good after one February. And a recurring event now moves its
  `event_ts_utc` forward after each send, so the list sorts on the next
  occurrence rather than on the date the series originally started.

No database migration is needed. Documents written before this change carry no
`all_day` and no `reminders`; they are read as all-day events whose reminder is
rebuilt from the existing reminder_hour / reminder_minute pair.

Nothing in the Mini App changes yet — that is batch 3b. Once this is deployed
the app behaves exactly as it does today, which is the point: the schema and
the interface move one at a time, so a regression has only one place to hide.

Safe to run twice. Every file is checked against a hash of the version this
script was built against; anything unexpected is reported and left untouched.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
report: list[str] = []


def log(status: str, message: str) -> None:
    report.append(f"  {status:<9} {message}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CONTENT_1 = r'''from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jdatetime

logger = logging.getLogger("tm_pro.dates")

REPEAT_VALUES = {"none", "daily", "weekly", "monthly", "yearly"}

# A reminder may sit at most 30 days ahead of its event.
MAX_OFFSET_MINUTES = 60 * 24 * 30
# Enough to walk a daily event roughly 11 years forward before giving up.
MAX_SCHEDULE_STEPS = 4000

DEFAULT_REMINDER_HOUR = 9
DEFAULT_REMINDER_MINUTE = 0


def safe_zoneinfo(tz_name: str | None) -> tuple[ZoneInfo, str]:
    try:
        if tz_name:
            return ZoneInfo(tz_name), tz_name
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("Invalid timezone received: %s", tz_name)

    return ZoneInfo("UTC"), "UTC"


def to_jalali(date_iso: str) -> str:
    try:
        parsed = datetime.strptime(date_iso, "%Y-%m-%d")
        jalali_date = jdatetime.date.fromgregorian(date=parsed.date())
        return jalali_date.strftime("%Y/%m/%d")
    except Exception:
        return date_iso


def expire_for_repeat(anchor: datetime, repeat: str) -> datetime:
    delta_map = {
        "none": timedelta(days=30),
        "daily": timedelta(days=2),
        "weekly": timedelta(days=10),
        "monthly": timedelta(days=40),
        "yearly": timedelta(days=400),
    }
    return anchor + delta_map.get(repeat, timedelta(days=30))


def repeat_label(repeat: str) -> str:
    return {
        "none": "One-time",
        "daily": "🔁 Daily",
        "weekly": "🔁 Weekly",
        "monthly": "🔁 Monthly",
        "yearly": "🎂 Yearly",
    }.get(repeat, "One-time")


# ── Time of day ─────────────────────────────────────────────────────────────

def parse_time_hm(value: str) -> tuple[int, int]:
    """'14:30' -> (14, 30). Raises ValueError on anything else."""
    hour_str, separator, minute_str = (value or "").strip().partition(":")
    if not separator:
        raise ValueError("time must look like HH:MM")

    hour, minute = int(hour_str), int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time out of range")

    return hour, minute


def format_time_hm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


# ── Occurrences ─────────────────────────────────────────────────────────────

def build_occurrence(
    date_str: str,
    tz: ZoneInfo,
    all_day: bool = True,
    time_hm: str | None = None,
) -> datetime:
    """The instant the event itself starts, in UTC.

    All-day events anchor at local midnight, which is what the day countdown
    and the list ordering expect. A timed event anchors at its own local time.
    """
    base = datetime.strptime(date_str, "%Y-%m-%d")

    hour, minute = 0, 0
    if not all_day and time_hm:
        hour, minute = parse_time_hm(time_hm)

    local = base.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)
    return local.astimezone(timezone.utc)


def shift_month(reference: datetime, anchor_day: int, months_ahead: int = 1) -> datetime:
    """Move whole months, re-anchoring on the original day of the month.

    Anchoring on `anchor_day` rather than on the previous result is what stops
    a monthly event on the 31st from landing on the 28th after one February
    and then staying there for good.
    """
    month = reference.month - 1 + months_ahead
    year = reference.year + month // 12
    month = month % 12 + 1
    day = min(anchor_day, monthrange(year, month)[1])

    return reference.replace(year=year, month=month, day=day, second=0, microsecond=0)


def advance_occurrence(
    occurrence_utc: datetime,
    repeat: str,
    tz: ZoneInfo,
    anchor_day: int,
) -> datetime | None:
    """One repeat period forward.

    Steps on the wall clock rather than on absolute time, so an event at 09:00
    stays at 09:00 across a daylight-saving change instead of drifting to 08:00.
    """
    if repeat == "none":
        return None

    local = occurrence_utc.astimezone(tz).replace(tzinfo=None)

    if repeat == "daily":
        nxt = local + timedelta(days=1)
    elif repeat == "weekly":
        nxt = local + timedelta(weeks=1)
    elif repeat == "monthly":
        nxt = shift_month(local, anchor_day, 1)
    elif repeat == "yearly":
        try:
            nxt = local.replace(year=local.year + 1)
        except ValueError:
            # 29 February in a non-leap year.
            nxt = local.replace(year=local.year + 1, month=3, day=1)
    else:
        return None

    return nxt.replace(tzinfo=tz).astimezone(timezone.utc)


# ── Reminders ───────────────────────────────────────────────────────────────

def normalize_reminders(
    raw: object,
    all_day: bool = True,
    legacy_hour: int = DEFAULT_REMINDER_HOUR,
    legacy_minute: int = DEFAULT_REMINDER_MINUTE,
) -> list[dict]:
    """Return the reminder list in canonical form.

    Accepts the stored list as well as the older reminder_hour / reminder_minute
    pair, so documents written before this field existed keep working with no
    migration step.

    An absolute reminder fires at a wall-clock time on the day of the event; a
    relative one fires a fixed number of minutes before it starts. All-day
    events only take absolute reminders, because "15 minutes before" has no
    meaning for a day that carries no time.
    """
    specs: list[dict] = []

    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("mode", "absolute")).lower() == "relative":
            offset = int(item.get("offset_minutes", 0) or 0)
            specs.append({
                "mode": "relative",
                "offset_minutes": max(0, min(offset, MAX_OFFSET_MINUTES)),
            })
        else:
            specs.append({
                "mode": "absolute",
                "hour": max(0, min(int(item.get("hour", legacy_hour) or 0), 23)),
                "minute": max(0, min(int(item.get("minute", legacy_minute) or 0), 59)),
            })

    if all_day:
        specs = [spec for spec in specs if spec["mode"] == "absolute"]

    if not specs:
        specs = [{
            "mode": "absolute",
            "hour": max(0, min(int(legacy_hour), 23)),
            "minute": max(0, min(int(legacy_minute), 59)),
        }]

    return specs


def reminder_fire_time(spec: dict, occurrence_utc: datetime, tz: ZoneInfo) -> datetime:
    """When a single reminder fires, in UTC."""
    if spec.get("mode") == "relative":
        return occurrence_utc - timedelta(minutes=int(spec.get("offset_minutes", 0)))

    local = occurrence_utc.astimezone(tz).replace(
        hour=int(spec.get("hour", DEFAULT_REMINDER_HOUR)),
        minute=int(spec.get("minute", DEFAULT_REMINDER_MINUTE)),
        second=0,
        microsecond=0,
    )
    return local.astimezone(timezone.utc)


def earliest_fire(
    reminders: list[dict],
    occurrence_utc: datetime,
    tz: ZoneInfo,
    after: datetime | None = None,
) -> datetime | None:
    """Earliest reminder for this occurrence, optionally only ones still ahead."""
    times = [reminder_fire_time(spec, occurrence_utc, tz) for spec in reminders]
    if after is not None:
        times = [moment for moment in times if moment > after]

    return min(times) if times else None


# ── Scheduling ──────────────────────────────────────────────────────────────

def _past_until(occurrence_utc: datetime, tz: ZoneInfo, repeat_until: str | None) -> bool:
    if not repeat_until:
        return False
    return occurrence_utc.astimezone(tz).date().isoformat() > repeat_until


def next_schedule(
    *,
    occurrence_utc: datetime,
    anchor_day: int,
    tz: ZoneInfo,
    reminders: list[dict],
    repeat: str,
    repeat_until: str | None = None,
    now: datetime | None = None,
    max_steps: int = MAX_SCHEDULE_STEPS,
) -> tuple[datetime, datetime | None]:
    """Walk forward to the next occurrence that still has a reminder ahead of it.

    Returns (occurrence, fire_time). A fire_time of None means the series is
    finished: either it does not repeat, or it ran past repeat_until.
    """
    now = now or datetime.now(timezone.utc)
    occurrence = occurrence_utc

    for _ in range(max_steps):
        nxt = advance_occurrence(occurrence, repeat, tz, anchor_day)
        if nxt is None:
            return occurrence, None

        occurrence = nxt
        if _past_until(occurrence, tz, repeat_until):
            return occurrence, None

        fire = earliest_fire(reminders, occurrence, tz, after=now)
        if fire is not None:
            return occurrence, fire

    logger.error("next_schedule exceeded %s steps (repeat=%s)", max_steps, repeat)
    return occurrence, None


def first_schedule(
    *,
    date_str: str,
    tz: ZoneInfo,
    all_day: bool,
    time_hm: str | None,
    reminders: list[dict],
    repeat: str,
    repeat_until: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime | None]:
    """Occurrence and next reminder time for an event that was just saved."""
    now = now or datetime.now(timezone.utc)
    occurrence = build_occurrence(date_str, tz, all_day, time_hm)

    if repeat == "none":
        # Unchanged behaviour for one-off events: if the reminder time has
        # already passed, it still fires on the next worker pass rather than
        # being dropped silently.
        return occurrence, earliest_fire(reminders, occurrence, tz)

    if not _past_until(occurrence, tz, repeat_until):
        fire = earliest_fire(reminders, occurrence, tz, after=now)
        if fire is not None:
            return occurrence, fire

    return next_schedule(
        occurrence_utc=occurrence,
        anchor_day=int(date_str[8:10]),
        tz=tz,
        reminders=reminders,
        repeat=repeat,
        repeat_until=repeat_until,
        now=now,
    )
'''

CONTENT_2 = r'''from __future__ import annotations

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
'''

CONTENT_3 = r'''from __future__ import annotations

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


class ListEventsRequest(InitDataPayload):
    skip: int = Field(default=0, ge=0, le=5000)


class EventBaseRequest(InitDataPayload):
    title: str = Field(..., min_length=1, max_length=200)
    date: str = Field(..., min_length=10, max_length=10)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    repeat: RepeatType = "none"
    repeat_until: str | None = Field(default=None)
    category: CategoryType = "general"
    note: str = Field(default="", max_length=2000)
    pinned: bool = False
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
'''

CONTENT_4 = r'''from __future__ import annotations

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
    all_day: bool = True
    time_hm: str | None = None
    reminders: list[ReminderSpec] = Field(default_factory=list)
    reminder_hour: int = 9
    reminder_minute: int = 0
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
'''

CONTENT_5 = r'''"""
app/services/events.py — Fixed v2.0
Fixes:
  - Removed duplicate 'import logging' statement
  - Cleaner import order (PEP 8)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import Settings, get_settings
from app.db import get_events_collection, get_users_collection
from app.schemas.requests import (
    AddEventRequest,
    EditEventRequest,
    ListEventsRequest,
    PinEventRequest,
    SaveNoteRequest,
)
from app.schemas.responses import EventOut
from app.utils.dates import (
    expire_for_repeat,
    first_schedule,
    normalize_reminders,
    safe_zoneinfo,
    to_jalali,
)
from app.utils.ids import safe_object_id

logger = logging.getLogger("tm_pro.events")

PAGE_SIZE = 50

VALID_REPEATS = {"none", "daily", "weekly", "monthly", "yearly"}
VALID_CATEGORIES = {
    "general", "birthday", "work", "family",
    "health", "travel", "finance", "study", "other",
}


def serialize_event(doc: dict) -> EventOut:
    date_iso = doc.get("date_iso", "")
    all_day = bool(doc.get("all_day", True))
    return EventOut(
        id=str(doc["_id"]),
        title=doc.get("title", ""),
        date_iso=date_iso,
        date_jalali=to_jalali(date_iso),
        repeat=doc.get("repeat", "none"),
        notify_status=doc.get("notify_status", "pending"),
        tz_name=doc.get("tz_name", "UTC"),
        category=doc.get("category", "general"),
        pinned=bool(doc.get("pinned", False)),
        note=doc.get("note", ""),
        all_day=all_day,
        time_hm=doc.get("time_hm"),
        # Documents written before this field existed have no "reminders" key;
        # normalize_reminders rebuilds it from the legacy hour/minute pair, so
        # no migration pass over the collection is needed.
        reminders=normalize_reminders(
            doc.get("reminders"),
            all_day=all_day,
            legacy_hour=doc.get("reminder_hour", 9),
            legacy_minute=doc.get("reminder_minute", 0),
        ),
        reminder_hour=doc.get("reminder_hour", 9),
        reminder_minute=doc.get("reminder_minute", 0),
        repeat_until=doc.get("repeat_until"),
    )


def _normalize_event_input(
    payload: AddEventRequest | EditEventRequest,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()

    title    = payload.title.strip()
    repeat   = payload.repeat.strip().lower()
    category = payload.category.strip().lower()
    note     = payload.note.strip()

    if len(title) > settings.max_title_len:
        raise HTTPException(status_code=400, detail="TITLE_TOO_LONG")
    if len(note) > settings.max_note_len:
        raise HTTPException(status_code=400, detail="NOTE_TOO_LONG")
    if repeat not in VALID_REPEATS:
        raise HTTPException(status_code=400, detail="INVALID_REPEAT")
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="INVALID_CATEGORY")

    # repeat_until only makes sense for recurring events; on a one-time event
    # it's silently ignored rather than rejected (harmless combination — e.g.
    # a user switched repeat back to "none" without clearing this field).
    repeat_until = payload.repeat_until if repeat != "none" else None
    if repeat_until is not None and repeat_until < payload.date:
        raise HTTPException(status_code=400, detail="REPEAT_UNTIL_BEFORE_START_DATE")

    all_day = bool(payload.all_day)
    time_hm = (payload.time_hm or "").strip() or None
    if not all_day and time_hm is None:
        raise HTTPException(status_code=400, detail="TIME_REQUIRED")
    if all_day:
        time_hm = None

    reminders = normalize_reminders(
        [spec.model_dump() for spec in payload.reminders],
        all_day=all_day,
        legacy_hour=payload.reminder_hour,
        legacy_minute=payload.reminder_minute,
    )

    tz, tz_name = safe_zoneinfo(payload.timezone)
    try:
        event_utc, notify_utc = first_schedule(
            date_str=payload.date,
            tz=tz,
            all_day=all_day,
            time_hm=time_hm,
            reminders=reminders,
            repeat=repeat,
            repeat_until=repeat_until,
            now=datetime.now(timezone.utc),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_DATE") from exc

    # notify_utc is None only when there is nothing left to fire — a recurring
    # series that has already run past its repeat_until. Storing "pending" with
    # no time would leave the document stuck in the worker's queue for ever.
    first_absolute = next(
        (spec for spec in reminders if spec["mode"] == "absolute"),
        None,
    )

    return {
        "title":                title,
        "date_iso":             payload.date,
        "all_day":              all_day,
        "time_hm":              time_hm,
        "reminders":            reminders,
        "next_notify_at":       notify_utc,
        "event_ts_utc":         event_utc,
        "expire_at":            expire_for_repeat(notify_utc or event_utc, repeat),
        "repeat":               repeat,
        "tz_name":              tz_name,
        # Kept in sync for clients that still read the flat pair.
        "reminder_hour":        (first_absolute or {}).get("hour", payload.reminder_hour),
        "reminder_minute":      (first_absolute or {}).get("minute", payload.reminder_minute),
        "repeat_until":         repeat_until,
        "notify_status":        "pending" if notify_utc is not None else "done",
        "notify_attempts":      0,
        "processing_started_at": None,
        "category":             category,
        "note":                 note,
        "pinned":               payload.pinned,
    }


async def list_events_for_user(
    user_id: str,
    payload: ListEventsRequest,
) -> tuple[list[EventOut], bool]:
    events_coll = get_events_collection()

    cursor = (
        events_coll.find({"user_id": user_id})
        .sort([("pinned", -1), ("event_ts_utc", 1)])
        .skip(payload.skip)
        .limit(PAGE_SIZE + 1)
    )

    items: list[EventOut] = []
    async for event in cursor:
        items.append(serialize_event(event))

    has_more = len(items) > PAGE_SIZE
    if has_more:
        items = items[:PAGE_SIZE]

    return items, has_more


async def add_event_for_user(
    user_id: str,
    payload: AddEventRequest,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    events_coll = get_events_collection()
    users_coll = get_users_collection()

    logger.info(
        "add_event user_id=%s title=%r date=%s tz=%s",
        user_id, payload.title, payload.date, payload.timezone,
    )

    count = await events_coll.count_documents({"user_id": user_id})
    if count >= settings.max_events_per_user:
        raise HTTPException(status_code=400, detail="EVENT_LIMIT_REACHED")

    event_data = _normalize_event_input(payload, settings)
    now = datetime.now(timezone.utc)
    event_data.update({"user_id": user_id, "created_at": now, "updated_at": now})

    await users_coll.update_one(
        {"_id": user_id},
        {"$set": {"timezone": event_data["tz_name"], "updated_at": now}},
        upsert=True,
    )

    result = await events_coll.insert_one(event_data)
    logger.info("event inserted user_id=%s event_id=%s", user_id, result.inserted_id)


async def edit_event_for_user(
    user_id: str,
    payload: EditEventRequest,
    settings: Settings | None = None,
) -> None:
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    existing = await events_coll.find_one({"_id": oid, "user_id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    event_data = _normalize_event_input(payload, settings)
    event_data["updated_at"] = datetime.now(timezone.utc)

    await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": event_data},
    )


async def delete_event_for_user(user_id: str, event_id: str) -> None:
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    result = await events_coll.delete_one({"_id": oid, "user_id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")


async def save_note_for_user(
    user_id: str,
    payload: SaveNoteRequest,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    events_coll = get_events_collection()

    note = payload.note.strip()
    if len(note) > settings.max_note_len:
        raise HTTPException(status_code=400, detail="NOTE_TOO_LONG")

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    result = await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": {"note": note, "updated_at": datetime.now(timezone.utc)}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    return note


async def set_pin_for_user(user_id: str, payload: PinEventRequest) -> bool:
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    result = await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": {"pinned": payload.pinned, "updated_at": datetime.now(timezone.utc)}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    return payload.pinned
'''

CONTENT_6 = r'''from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings, get_settings
from app.db import get_events_collection
from app.utils.dates import (
    earliest_fire,
    expire_for_repeat,
    next_schedule,
    normalize_reminders,
    repeat_label,
    safe_zoneinfo,
    to_jalali,
)
from app.utils.ids import object_id_str, safe_object_id

logger = logging.getLogger("tm_pro.reminders")


async def recover_stale_processing(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    events_coll = get_events_collection()

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.stale_processing_secs)
    result = await events_coll.update_many(
        {
            "notify_status": "processing",
            "processing_started_at": {"$lte": cutoff},
        },
        {
            "$set": {"notify_status": "pending"},
            "$unset": {"processing_started_at": ""},
        },
    )

    return int(result.modified_count)


def event_reminder_specs(evt: dict) -> list[dict]:
    """Reminder list for a stored event, rebuilt from the legacy hour/minute
    pair when the document predates the `reminders` field."""
    return normalize_reminders(
        evt.get("reminders"),
        all_day=bool(evt.get("all_day", True)),
        legacy_hour=evt.get("reminder_hour", 9),
        legacy_minute=evt.get("reminder_minute", 0),
    )


def build_reminder_text(evt: dict) -> str:
    repeat = evt.get("repeat", "none")
    repeat_text = repeat_label(repeat)
    date_iso = evt.get("date_iso", "")
    jalali_date = to_jalali(date_iso)
    category = evt.get("category", "general")
    pin_mark = "📌 " if evt.get("pinned") else ""
    title = html.escape(evt.get("title", ""))

    time_line = ""
    if not evt.get("all_day", True) and evt.get("time_hm"):
        time_line = f"🕒 {html.escape(str(evt['time_hm']))}\n"

    return (
        f"🔔 <b>Reminder</b>\n"
        f"{pin_mark}{title}\n"
        f"📅 {date_iso}  •  {jalali_date}\n"
        f"{time_line}"
        f"🏷️ {html.escape(category.title())}\n"
        f"🔄 {repeat_text}"
    )


def build_reminder_keyboard(evt: dict, settings: Settings) -> InlineKeyboardMarkup:
    event_id = object_id_str(evt["_id"])
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={event_id}"
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏰ Snooze 1h", callback_data=f"snooze1h:{event_id}"),
                InlineKeyboardButton("📖 Open", url=deep_link),
            ]
        ]
    )


async def handle_snooze_callback(
    event_id: str,
    telegram_user_id: int | str,
    seconds: int,
) -> bool:
    try:
        oid = safe_object_id(event_id)
    except ValueError:
        return False

    # user_id is persisted as a string everywhere (see app/services/auth.py),
    # while Telegram hands us an int on callback queries. MongoDB matches on
    # the exact BSON type, so without this cast the filter never matches and
    # every snooze silently fails.
    user_id = str(telegram_user_id)

    events_coll = get_events_collection()
    new_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    result = await events_coll.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {"$set": {"next_notify_at": new_time, "notify_status": "pending"}},
    )
    return result is not None


async def process_due_reminders(
    bot: Bot,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    events_coll = get_events_collection()

    await recover_stale_processing(settings)

    now = datetime.now(timezone.utc)
    cursor = (
        events_coll.find(
            {
                "next_notify_at": {"$lte": now},
                "notify_status": "pending",
            }
        )
        .sort("next_notify_at", 1)
        .limit(settings.reminder_batch_size)
    )

    processed = 0

    async for evt in cursor:
        claimed = await events_coll.find_one_and_update(
            {
                "_id": evt["_id"],
                "notify_status": "pending",
            },
            {
                "$set": {
                    "notify_status": "processing",
                    "processing_started_at": now,
                }
            },
        )

        if not claimed:
            continue

        try:
            await bot.send_message(
                chat_id=evt["user_id"],
                text=build_reminder_text(evt),
                parse_mode="HTML",
                reply_markup=build_reminder_keyboard(evt, settings),
            )

            repeat = evt.get("repeat", "none")
            tz, _ = safe_zoneinfo(evt.get("tz_name", "UTC"))
            occurrence = evt.get("event_ts_utc", now)
            if occurrence.tzinfo is None:
                occurrence = occurrence.replace(tzinfo=timezone.utc)

            if repeat != "none":
                specs = event_reminder_specs(evt)
                date_iso_raw = evt.get("date_iso", "")
                anchor_day = (
                    int(date_iso_raw[8:10]) if len(date_iso_raw) == 10 else occurrence.day
                )

                # Another reminder on the same occurrence may still be ahead.
                # Only one is exposed in the UI today, but the stored shape is a
                # list, so drain the current occurrence before moving on.
                next_occurrence = occurrence
                next_notify = earliest_fire(specs, occurrence, tz, after=now)

                if next_notify is None:
                    next_occurrence, next_notify = next_schedule(
                        occurrence_utc=occurrence,
                        anchor_day=anchor_day,
                        tz=tz,
                        reminders=specs,
                        repeat=repeat,
                        repeat_until=evt.get("repeat_until"),
                        now=now,
                    )

                if next_notify is None:
                    await events_coll.update_one(
                        {"_id": evt["_id"]},
                        {
                            "$set": {"notify_status": "done", "next_notify_at": None},
                            "$unset": {"processing_started_at": ""},
                        },
                    )
                    processed += 1
                    continue

                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "pending",
                            "next_notify_at": next_notify,
                            # Moving the occurrence forward as well keeps the
                            # list sorted on the NEXT date rather than on the
                            # date the series originally started.
                            "event_ts_utc": next_occurrence,
                            "expire_at": expire_for_repeat(next_notify, repeat),
                            "notify_attempts": 0,
                        },
                        "$unset": {"processing_started_at": ""},
                    },
                )
            else:
                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "done",
                            "next_notify_at": None,
                        },
                        "$unset": {"processing_started_at": ""},
                    },
                )

            processed += 1

        except Exception as exc:
            attempts = int(evt.get("notify_attempts", 0)) + 1
            status = "failed" if attempts >= 5 else "pending"

            await events_coll.update_one(
                {"_id": evt["_id"]},
                {
                    "$set": {
                        "notify_attempts": attempts,
                        "notify_status": status,
                    },
                    "$unset": {"processing_started_at": ""},
                },
            )
            logger.exception("Reminder send failed for event=%s error=%s", evt["_id"], exc)

    return processed
'''

CONTENT_7 = r'''from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.utils.dates import (
    advance_occurrence,
    build_occurrence,
    earliest_fire,
    expire_for_repeat,
    first_schedule,
    format_time_hm,
    next_schedule,
    normalize_reminders,
    parse_time_hm,
    reminder_fire_time,
    safe_zoneinfo,
    shift_month,
    to_jalali,
)

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


# ── Timezones and calendars ─────────────────────────────────────────────────

def test_safe_zoneinfo_returns_valid_zone() -> None:
    tz, name = safe_zoneinfo("Europe/Berlin")
    assert name == "Europe/Berlin"
    assert tz is not None


def test_safe_zoneinfo_falls_back_to_utc() -> None:
    tz, name = safe_zoneinfo("Not/AZone")
    assert name == "UTC"
    assert tz is not None


def test_to_jalali_returns_formatted_value() -> None:
    assert to_jalali("2026-04-20") == "1405/01/31"


def test_to_jalali_fallbacks_for_invalid_input() -> None:
    assert to_jalali("nonsense") == "nonsense"


def test_expire_for_repeat_none_is_future() -> None:
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert expire_for_repeat(anchor, "none") > anchor


# ── Time of day ─────────────────────────────────────────────────────────────

def test_parse_time_hm_roundtrip() -> None:
    assert parse_time_hm("14:30") == (14, 30)
    assert format_time_hm(14, 30) == "14:30"
    assert format_time_hm(9, 5) == "09:05"


def test_parse_time_hm_rejects_bad_values() -> None:
    for bad in ["1430", "25:00", "12:99", ""]:
        try:
            parse_time_hm(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def test_build_occurrence_all_day_anchors_at_local_midnight() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=True)
    assert occurrence.tzinfo is not None
    assert occurrence.astimezone(BERLIN).hour == 0


def test_build_occurrence_uses_the_given_time() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:30")
    local = occurrence.astimezone(BERLIN)
    assert (local.hour, local.minute) == (14, 30)


# ── Recurrence ──────────────────────────────────────────────────────────────

def test_shift_month_reanchors_on_the_original_day() -> None:
    """A monthly event on the 31st must not stick to the 28th after February."""
    jan = datetime(2026, 1, 31, 9, 0)
    feb = shift_month(jan, anchor_day=31, months_ahead=1)
    mar = shift_month(feb, anchor_day=31, months_ahead=1)
    assert (feb.month, feb.day) == (2, 28)
    assert (mar.month, mar.day) == (3, 31)


def test_advance_occurrence_keeps_wall_clock_across_dst() -> None:
    """Berlin leaves DST on 25 October 2026; 09:00 must stay 09:00."""
    before = build_occurrence("2026-10-24", BERLIN, all_day=False, time_hm="09:00")
    after = advance_occurrence(before, "daily", BERLIN, anchor_day=24)
    assert after is not None
    assert after.astimezone(BERLIN).hour == 9


def test_advance_occurrence_returns_none_for_one_off_events() -> None:
    occurrence = build_occurrence("2026-06-15", UTC)
    assert advance_occurrence(occurrence, "none", UTC, anchor_day=15) is None


def test_advance_occurrence_handles_29_february() -> None:
    leap = build_occurrence("2028-02-29", UTC, all_day=False, time_hm="09:00")
    nxt = advance_occurrence(leap, "yearly", UTC, anchor_day=29)
    assert nxt is not None
    assert (nxt.month, nxt.day) == (3, 1)


# ── Reminders ───────────────────────────────────────────────────────────────

def test_normalize_reminders_falls_back_to_the_legacy_pair() -> None:
    specs = normalize_reminders(None, all_day=True, legacy_hour=7, legacy_minute=30)
    assert specs == [{"mode": "absolute", "hour": 7, "minute": 30}]


def test_normalize_reminders_drops_relative_specs_on_all_day_events() -> None:
    specs = normalize_reminders(
        [{"mode": "relative", "offset_minutes": 15}],
        all_day=True,
        legacy_hour=9,
        legacy_minute=0,
    )
    assert specs == [{"mode": "absolute", "hour": 9, "minute": 0}]


def test_normalize_reminders_keeps_relative_specs_on_timed_events() -> None:
    specs = normalize_reminders(
        [{"mode": "relative", "offset_minutes": 60}],
        all_day=False,
    )
    assert specs == [{"mode": "relative", "offset_minutes": 60}]


def test_normalize_reminders_clamps_out_of_range_values() -> None:
    specs = normalize_reminders([{"mode": "absolute", "hour": 99, "minute": -5}], all_day=True)
    assert specs == [{"mode": "absolute", "hour": 23, "minute": 0}]


def test_relative_reminder_fires_before_the_event() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:30")
    fire = reminder_fire_time({"mode": "relative", "offset_minutes": 60}, occurrence, BERLIN)
    assert occurrence - fire == timedelta(minutes=60)


def test_absolute_reminder_fires_on_the_day_of_the_event() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=True)
    fire = reminder_fire_time({"mode": "absolute", "hour": 9, "minute": 0}, occurrence, BERLIN)
    local = fire.astimezone(BERLIN)
    assert (local.date().isoformat(), local.hour) == ("2026-06-15", 9)


def test_earliest_fire_picks_the_soonest_and_can_skip_the_past() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:00")
    specs = [
        {"mode": "relative", "offset_minutes": 60},
        {"mode": "relative", "offset_minutes": 1440},
    ]
    assert earliest_fire(specs, occurrence, BERLIN) == occurrence - timedelta(minutes=1440)

    cutoff = occurrence - timedelta(minutes=120)
    assert earliest_fire(specs, occurrence, BERLIN, after=cutoff) == occurrence - timedelta(minutes=60)


# ── Scheduling ──────────────────────────────────────────────────────────────

def test_first_schedule_for_a_future_one_off_event() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    occurrence, fire = first_schedule(
        date_str="2026-06-15",
        tz=BERLIN,
        all_day=False,
        time_hm="14:30",
        reminders=[{"mode": "relative", "offset_minutes": 30}],
        repeat="none",
        now=now,
    )
    assert fire == occurrence - timedelta(minutes=30)
    assert fire > now


def test_first_schedule_rolls_a_recurring_event_past_now() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    _, fire = first_schedule(
        date_str="2026-06-15",
        tz=UTC,
        all_day=True,
        time_hm=None,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="daily",
        now=now,
    )
    assert fire is not None
    assert fire > now


def test_first_schedule_returns_none_once_repeat_until_has_passed() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    _, fire = first_schedule(
        date_str="2026-06-15",
        tz=UTC,
        all_day=True,
        time_hm=None,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="daily",
        repeat_until="2026-06-15",
        now=now,
    )
    assert fire is None


def test_next_schedule_moves_to_the_following_occurrence() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    occurrence = build_occurrence("2026-06-15", UTC, all_day=True)
    next_occurrence, fire = next_schedule(
        occurrence_utc=occurrence,
        anchor_day=15,
        tz=UTC,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="monthly",
        now=now,
    )
    assert next_occurrence.astimezone(UTC).date().isoformat() == "2026-07-15"
    assert fire is not None and fire > now
'''

CONTENT_8 = r'''from __future__ import annotations

import pytest

from app.schemas.responses import EventOut


@pytest.mark.anyio
async def test_health_endpoint(async_client) -> None:
    from app.routes import health as health_module

    async def fake_ping_database() -> bool:
        return True

    original = health_module.ping_database
    health_module.ping_database = fake_ping_database
    try:
        response = await async_client.get("/health")
    finally:
        health_module.ping_database = original

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert "ts" in data


@pytest.mark.anyio
async def test_api_list_returns_targets(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_list_events_for_user(user_id: str, payload):
        return (
            [
                EventOut(
                    id="evt1",
                    title="Birthday",
                    date_iso="2026-04-20",
                    date_jalali="1405/01/31",
                    repeat="yearly",
                    notify_status="pending",
                    tz_name="UTC",
                    category="birthday",
                    pinned=True,
                    note="Cake",
                )
            ],
            False,
        )

    original_auth = events_module.get_authenticated_user_id
    original_list = events_module.list_events_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.list_events_for_user = fake_list_events_for_user

    try:
        response = await async_client.post(
            "/api/list",
            json={"initData": "dummy", "skip": 0},
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.list_events_for_user = original_list

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["targets"]) == 1
    assert data["targets"][0]["title"] == "Birthday"
    assert data["meta"]["returned"] == 1
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_api_add_success(async_client) -> None:
    from app.routes import events as events_module

    captured: dict = {}

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_add_event_for_user(user_id: str, payload) -> None:
        captured["user_id"] = user_id
        captured["title"] = payload.title
        captured["date"] = payload.date

    original_auth = events_module.get_authenticated_user_id
    original_add = events_module.add_event_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.add_event_for_user = fake_add_event_for_user

    try:
        response = await async_client.post(
            "/api/add",
            json={
                "initData": "dummy",
                "title": "Doctor Visit",
                "date": "2026-05-01",
                "timezone": "UTC",
                "repeat": "none",
                "category": "health",
                "note": "",
                "pinned": False,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.add_event_for_user = original_add

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert captured["user_id"] == "123"
    assert captured["title"] == "Doctor Visit"
    assert captured["date"] == "2026-05-01"


@pytest.mark.anyio
async def test_api_add_rejects_invalid_payload(async_client) -> None:
    response = await async_client.post(
        "/api/add",
        json={
            "initData": "dummy",
            "title": "",
            "date": "2026/05/01",
            "timezone": "UTC",
            "repeat": "none",
            "category": "health",
            "note": "",
            "pinned": False,
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_api_note_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_save_note_for_user(user_id: str, payload) -> str:
        assert user_id == "123"
        assert payload.event_id == "event123"
        return payload.note

    original_auth = events_module.get_authenticated_user_id
    original_save = events_module.save_note_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.save_note_for_user = fake_save_note_for_user

    try:
        response = await async_client.post(
            "/api/note",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "note": "Buy candles",
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.save_note_for_user = original_save

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["note"] == "Buy candles"


@pytest.mark.anyio
async def test_api_pin_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_set_pin_for_user(user_id: str, payload) -> bool:
        assert user_id == "123"
        return payload.pinned

    original_auth = events_module.get_authenticated_user_id
    original_pin = events_module.set_pin_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.set_pin_for_user = fake_set_pin_for_user

    try:
        response = await async_client.post(
            "/api/pin",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "pinned": True,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.set_pin_for_user = original_pin

    assert response.status_code == 200
    assert response.json() == {"success": True, "pinned": True}


@pytest.mark.anyio
async def test_root_redirects_to_webapp(async_client) -> None:
    response = await async_client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/webapp"


def _event_payload(**overrides):
    from app.schemas.requests import AddEventRequest

    base = {
        "initData": "dummy",
        "title": "Standup",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "none",
    }
    base.update(overrides)
    return AddEventRequest(**base)


def test_normalize_event_input_defaults_to_an_all_day_event() -> None:
    """A client that has not been updated sends neither all_day nor reminders;
    it must keep producing exactly the old schedule."""
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(_event_payload(reminder_hour=7, reminder_minute=30))

    assert doc["all_day"] is True
    assert doc["time_hm"] is None
    assert doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]
    assert doc["reminder_hour"] == 7
    assert doc["notify_status"] == "pending"


def test_normalize_event_input_schedules_a_relative_reminder() -> None:
    from datetime import timedelta

    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(
            all_day=False,
            time_hm="14:30",
            reminders=[{"mode": "relative", "offset_minutes": 60}],
        )
    )

    assert doc["all_day"] is False
    assert doc["time_hm"] == "14:30"
    assert doc["event_ts_utc"] - doc["next_notify_at"] == timedelta(minutes=60)


def test_normalize_event_input_requires_a_time_when_not_all_day() -> None:
    from fastapi import HTTPException

    from app.services.events import _normalize_event_input

    with pytest.raises(HTTPException) as excinfo:
        _normalize_event_input(_event_payload(all_day=False))

    assert excinfo.value.detail == "TIME_REQUIRED"


def test_normalize_event_input_marks_a_finished_series_done() -> None:
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(date="2020-01-01", repeat="daily", repeat_until="2020-01-02")
    )

    assert doc["next_notify_at"] is None
    assert doc["notify_status"] == "done"
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "app/utils/dates.py",
        "cc64e896710c9e1ee81f5475c1030ff643172c294f8086e4b041f52b4d405dbf",
        "fe7e11c4e3c7a1dffe2c3b0be4dfb3d1d44a42bd9682077033217be1af47d68a",
        CONTENT_1,
    ),
    (
        "app/schemas/common.py",
        "f0671c21ccd4f91a3cc82c06a1c9fb57aadbe42171c9d9580cf64a8b5e246a62",
        "1421dc3f3b02f79f220fdd207f7943c29d8a1c6d5a61003048bc54cc7f089d5e",
        CONTENT_2,
    ),
    (
        "app/schemas/requests.py",
        "8a14eadb39b722a0ae691de186983ebb953598ac2ec45c581aacedda787e1ecf",
        "02897f33c63dd0d091aebfd8454e35ba93affe395382fe49b939ae7862bbb3e9",
        CONTENT_3,
    ),
    (
        "app/schemas/responses.py",
        "935199e829cf715b91b050c42da75f3201cc4f66a4698198c04c31c3346c0a2c",
        "a2a9c975c24f4f83cd1d3dc20e02218efd2c18ea0a778e160cc71133cff456b1",
        CONTENT_4,
    ),
    (
        "app/services/events.py",
        "96e26b80a7d387262acdca22a143d15713e7242e8f391c3a006ba5af9cff80a2",
        "9696fdbb2bb24701b65b009e3e7c3e023ded1b140045640f3db55b22f336a1cb",
        CONTENT_5,
    ),
    (
        "app/services/reminders.py",
        "55084bf728bb05f7bcf21607f12382999b292603d34ae6e7f8ff493b06195535",
        "938dec9c1f72974501a3be47edcd45d94692217aa7f74d9c156ce1babf16e862",
        CONTENT_6,
    ),
    (
        "tests/test_dates.py",
        "40ed2fbda2da97648393c51120eb9f958a24340abb38e922007067befe42212f",
        "108c8e5e5721dcff4c11eb82add1263b5a88b4b236fbd38e01502a9d127e1516",
        CONTENT_7,
    ),
    (
        "tests/test_events_api.py",
        "4ab6ed6cc0ed98a586d27ec09f5df673f2be2cb57fa7acbf63106d964c478298",
        "8b188a0d325dd457f6a4cc3a8dde3bd3f45b69770730444d90606f2250e8f446",
        CONTENT_8,
    ),
]


def apply_files() -> None:
    for rel, expected_sha, new_sha, content in FILES:
        path = ROOT / rel
        if not path.exists():
            log("MISSING", f"{rel} — not found, skipped")
            continue

        digest = sha(path.read_bytes())

        if digest == new_sha:
            log("skipped", f"{rel} (already applied)")
            continue

        if digest != expected_sha:
            log("MANUAL", f"{rel} — not the version this script expects, left untouched")
            continue

        path.write_bytes(content.encode("utf-8"))
        log("updated", rel)


def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    report.append("\nApplying batch 3a — times on events")
    apply_files()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  ruff check . ; pytest -q          # expect 61 passing\n"
        "  git add -A && git rm --cached fix_batch3.py 2>/dev/null\n"
        '  git commit -m "Add event times and reminder specs"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Some files were left untouched — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
