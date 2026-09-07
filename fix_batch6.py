#!/usr/bin/env python3
"""
fix_batch6.py — TimeManager Pro, batch 6: two live bugs, an archive, and a
typeface that can actually render Persian.

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch6.py
    python -m ruff check . ; python -m pytest -q

1. The Jalali converter threw on every real date

  static/app.js had:

      let gy = jy > 979 ? (gy = 1600, jy -= 979, 1600) : ...

  which assigns to gy inside gy's own let initialiser — a temporal dead zone
  violation. Any Jalali year above 979, meaning every date a user would type,
  raised a ReferenceError inside the change handler. Typing a Jalali date
  silently never updated the Gregorian field.

  Both converters are replaced with the Julian-day algorithm, checked against
  the jdatetime output the server itself produces for all 25,567 days from 1990
  to 2060: no differences, no round-trip failures. A second bug turned up while
  testing month lengths — the leap flag counts years since the last leap year,
  so zero rather than one marks a leap year, and Esfand was getting 30 days in
  ordinary years.

2. Recurring events displayed the date they started

  Batch 3a made the worker advance event_ts_utc so the list sorted on the next
  occurrence, but the cards and the countdown still read date_iso. A yearly
  birthday added in 2020 sorted correctly to the top while announcing "6 years
  ago". The response now carries next_date_iso and next_date_jalali alongside
  date_iso, so the series start is kept — for a birthday that is the birth date
  and worth keeping — while the countdown follows the occurrence ahead.

3. One-off events are archived instead of deleted

  A TTL index was removing every non-repeating event 30 days after its reminder
  fired. Those events keep no expiry now, and a startup migration clears the
  one already sitting on existing documents — without it the archive would lose
  everything older than 30 days on the first night. Archived events leave the
  main list and appear under a new Past filter; a pinned one stays in the main
  list, since pinning is precisely the request to keep something in view.

4. Self-hosted Vazirmatn, replacing Plus Jakarta Sans from Google Fonts

  Plus Jakarta Sans carries no Persian glyphs, so every Persian word was
  falling back to whatever the device had. Vazirmatn covers Latin, Persian and
  both digit sets in one family, which matters because this interface mixes
  them constantly: "1405/01/31" and "14:30" sit inside Persian sentences, and
  two families would render the digits and the words in different designs on
  different baselines. One family also halves the payload and removes a Google
  Fonts round trip that is slow or unreachable for much of this audience.

  The trade-off, stated plainly: the Latin interface changes typeface. Plus
  Jakarta is a geometric display face, Vazirmatn a neutral interface one. Worth
  looking at before you decide you like it.

  This step needs npm and network access. The files come from a pinned package
  version and every one is checked against a SHA-256 recorded here; a mismatch
  or a missing npm is reported and skipped, leaving the rest applied.

Safe to run twice.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
report: list[str] = []


def log(status: str, message: str) -> None:
    report.append(f"  {status:<9} {message}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FONT_PACKAGE = "vazirmatn@33.0.3"
FONT_DIR = ROOT / "static" / "fonts"
FONTS = {
    "Vazirmatn-Regular.woff2":   "e382101336c6eb32cfb31381c027d02d2e0354bad08f6a395d4088beb3db3d91",
    "Vazirmatn-Medium.woff2":    "3333e31188a2b628db8780ca22fd5aad85bc083ccee9beb8d4d52db18cb98d48",
    "Vazirmatn-SemiBold.woff2":  "6a39a3c25eb18503cad590527b95bb5d4062b889a7ebbd3f01b0488d239e0499",
    "Vazirmatn-Bold.woff2":      "836fae7d42d83faa249bc00e0099592be98a1fa260d22d82f269b6091e585627",
    "Vazirmatn-ExtraBold.woff2": "cd67558bbca0ad319b89e3b2edb8a914f87f864951d7a9d24e1404cbf3b45b02",
}


CONTENT_1 = r'''from __future__ import annotations

import logging
from typing import Optional

import certifi
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.db")

_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError("Database client is not initialized")
    return _client


def get_database() -> AsyncIOMotorDatabase:
    if _database is None:
        raise RuntimeError("Database is not initialized")
    return _database


def get_events_collection() -> AsyncIOMotorCollection:
    return get_database()["events"]


def get_users_collection() -> AsyncIOMotorCollection:
    return get_database()["users"]


async def connect_to_mongo(settings: Settings | None = None) -> AsyncIOMotorDatabase:
    global _client, _database

    if _client is not None and _database is not None:
        return _database

    settings = settings or get_settings()

    _client = AsyncIOMotorClient(
        settings.mongo_uri,
        tlsCAFile=certifi.where(),
    )
    _database = _client[settings.mongo_db_name]

    await _database.command("ping")
    logger.info("MongoDB connected: db=%s", settings.mongo_db_name)

    return _database


async def close_mongo_connection() -> None:
    global _client, _database

    if _client is not None:
        _client.close()
        logger.info("MongoDB connection closed")

    _client = None
    _database = None


async def ensure_indexes(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    events = get_events_collection()

    await events.create_index("expire_at", expireAfterSeconds=0)
    await events.create_index([("notify_status", 1), ("next_notify_at", 1)])
    await events.create_index("user_id")
    await events.create_index([("user_id", 1), ("pinned", -1), ("event_ts_utc", 1)])
    await events.create_index([("user_id", 1), ("category", 1)])
    await events.create_index([("notify_status", 1), ("processing_started_at", 1)])

    rate_limits = get_database()["rate_limits"]
    await rate_limits.create_index("ts", expireAfterSeconds=60)
    await rate_limits.create_index([("user_id", 1), ("bucket", 1)], unique=True)

    logger.info("MongoDB indexes ensured for app=%s", settings.app_name)


async def backfill_jalali_dates(batch_size: int = 500, max_batches: int = 200) -> int:
    """Fill date_jalali on documents written before the field was stored.

    The conversion cannot be expressed in an aggregation pipeline, so this walks
    the documents in Python. After the first successful run the initial query
    matches nothing and the function returns straight away, which is why it is
    safe to call on every boot.
    """
    from pymongo import UpdateOne

    from app.utils.dates import to_jalali

    events = get_events_collection()
    updated = 0

    for _ in range(max_batches):
        cursor = events.find(
            {"date_jalali": {"$exists": False}},
            {"date_iso": 1},
        ).limit(batch_size)

        operations = [
            UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"date_jalali": to_jalali(doc.get("date_iso", ""))}},
            )
            async for doc in cursor
        ]

        if not operations:
            break

        result = await events.bulk_write(operations, ordered=False)
        updated += result.modified_count

        if len(operations) < batch_size:
            break

    if updated:
        logger.info("Backfilled date_jalali on %s events", updated)

    return updated


async def stop_expiring_one_off_events() -> int:
    """Remove the TTL marker from one-off events written before they were kept.

    Those documents already carry an expire_at, so without this the archive
    would quietly lose every event whose reminder fired more than 30 days ago.
    A single update_many, and a no-op once it has run.
    """
    events = get_events_collection()
    result = await events.update_many(
        {"repeat": "none", "expire_at": {"$exists": True}},
        {"$unset": {"expire_at": ""}},
    )

    if result.modified_count:
        logger.info("Kept %s one-off events that were set to expire", result.modified_count)

    return result.modified_count


async def ping_database() -> bool:
    db = get_database()
    await db.command("ping")
    return True
'''

CONTENT_2 = r'''from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from telegram import Bot

from app.config import get_settings
from app.db import (
    backfill_jalali_dates,
    close_mongo_connection,
    connect_to_mongo,
    ensure_indexes,
    stop_expiring_one_off_events,
)
from app.routes.events import router as events_router
from app.routes.health import router as health_router
from app.routes.telegram import router as telegram_router
from app.routes.web import router as web_router

settings = get_settings()

root_level = getattr(logging, settings.log_level.upper(), logging.INFO)

logging.basicConfig(
    level=root_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if settings.app_env.lower() == "production":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("tm_pro.app")

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)

    try:
        async with Bot(token=settings.bot_token) as bot:
            me = await bot.get_me()
            logger.info("Runtime bot = @%s id=%s", me.username, me.id)

            webhook_url = f"{settings.webapp_base_url}/telegram/webhook"
            await bot.set_webhook(url=webhook_url, secret_token=settings.telegram_webhook_secret)
            logger.info("Telegram webhook set to %s", webhook_url)
    except Exception as exc:
        logger.warning("Runtime bot verification/webhook setup failed: %s", exc)

    await connect_to_mongo(settings)
    await ensure_indexes(settings)

    # One-off migration: a no-op on every boot after the first, and a failure
    # here must not stop the app from serving.
    try:
        await backfill_jalali_dates()
        await stop_expiring_one_off_events()
    except Exception:
        logger.exception("Startup migration failed; search or archiving may be incomplete")

    yield

    await close_mongo_connection()
    logger.info("Stopped %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health_router)
app.include_router(web_router)
app.include_router(events_router)
app.include_router(telegram_router)
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
from app.utils.text import regex_clause, search_variants

logger = logging.getLogger("tm_pro.events")

PAGE_SIZE = 50

VALID_REPEATS = {"none", "daily", "weekly", "monthly", "yearly"}
VALID_CATEGORIES = {
    "general", "birthday", "work", "family",
    "health", "travel", "finance", "study", "other",
}


def next_occurrence_iso(doc: dict) -> str:
    """The date of the occurrence this event is currently counting down to.

    The worker moves event_ts_utc forward after each send; date_iso stays on
    the day the series began.
    """
    event_ts = doc.get("event_ts_utc")
    if event_ts is None:
        return doc.get("date_iso", "")

    tz, _ = safe_zoneinfo(doc.get("tz_name"))
    if event_ts.tzinfo is None:
        event_ts = event_ts.replace(tzinfo=timezone.utc)
    return event_ts.astimezone(tz).date().isoformat()


def serialize_event(doc: dict) -> EventOut:
    date_iso = doc.get("date_iso", "")
    all_day = bool(doc.get("all_day", True))
    next_iso = next_occurrence_iso(doc)
    return EventOut(
        id=str(doc["_id"]),
        title=doc.get("title", ""),
        date_iso=date_iso,
        date_jalali=doc.get("date_jalali") or to_jalali(date_iso),
        repeat=doc.get("repeat", "none"),
        notify_status=doc.get("notify_status", "pending"),
        tz_name=doc.get("tz_name", "UTC"),
        category=doc.get("category", "general"),
        pinned=bool(doc.get("pinned", False)),
        note=doc.get("note", ""),
        next_date_iso=next_iso,
        next_date_jalali=to_jalali(next_iso) if next_iso != date_iso
                         else (doc.get("date_jalali") or to_jalali(date_iso)),
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
        "lang":                 payload.lang,
        # Stored, not just computed on read: the Jalali date has to be in the
        # document for a Mongo query to be able to search it.
        "date_jalali":          to_jalali(payload.date),
        "all_day":              all_day,
        "time_hm":              time_hm,
        "reminders":            reminders,
        "next_notify_at":       notify_utc,
        "event_ts_utc":         event_utc,
        # Only recurring series get a TTL, as a safety net against an abandoned
        # series growing for ever. A one-off event is kept: its reminder having
        # fired is not a reason to erase the user's record of it.
        **({"expire_at": expire_for_repeat(notify_utc or event_utc, repeat)}
           if repeat != "none" else {}),
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


def build_list_query(user_id: str, payload: ListEventsRequest) -> dict:
    """Mongo filter for one page of a user's events.

    Every branch keeps user_id in the filter, so the regex search can only ever
    scan the documents belonging to the caller.
    """
    query: dict = {"user_id": user_id}

    # A one-off event whose reminder has already been sent is archived: kept
    # for ever, but out of the way. Pinned ones stay in the main list, since
    # pinning is exactly the request to keep something in view.
    archived = {"repeat": "none", "notify_status": "done", "pinned": {"$ne": True}}

    if payload.filter == "past":
        query.update({"repeat": "none", "notify_status": "done"})
    else:
        query["$nor"] = [archived]

        if payload.filter == "pinned":
            query["pinned"] = True
        elif payload.filter != "all":
            query["category"] = payload.filter

    clauses: list[dict] = []
    for term in search_variants(payload.q):
        clauses.append(regex_clause("title", term))
        clauses.append(regex_clause("note", term))
        clauses.append(regex_clause("date_iso", term, case_insensitive=False))
        clauses.append(regex_clause("date_jalali", term, case_insensitive=False))

        # The old client-side search also matched the category label shown in
        # the UI, which is the stored value itself.
        matched = sorted(c for c in VALID_CATEGORIES if term.lower() in c)
        if matched:
            clauses.append({"category": {"$in": matched}})

    if clauses:
        query["$or"] = clauses

    return query


async def list_events_for_user(
    user_id: str,
    payload: ListEventsRequest,
) -> tuple[list[EventOut], bool]:
    events_coll = get_events_collection()

    cursor = (
        events_coll.find(build_list_query(user_id, payload))
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

CONTENT_6 = r'''/**
 * TimeManager Pro — app.js v2.0
 * Fixes applied:
 *  - event_id (was: eventid) in edit/delete/pin/note payloads
 *  - All countdown text translated to English (was: Persian)
 *  - window.confirm replaced with custom confirm dialog
 *  - All debug console.log removed
 *  - API field names: date_iso / date_jalali / notify_status / tz_name
 *  - Pinned badge text: "Pinned" (was: "سنجاق‌شده")
 *  - Skeleton loading state
 *  - Pagination / load-more
 *  - Countdown ring in detail view
 *  - Note character counter
 *  - Reminder hour field support
 *  - Reminder hour actually sent to the backend (was: silently dropped)
 *  - Haptic feedback on save/delete/pin/error (via Telegram WebApp SDK)
 *  - First-run onboarding overlay (3 steps, shown once via localStorage)
 */

(() => {
  "use strict";

  /* ── Telegram WebApp ────────────────────────────────── */
  const tg = window.Telegram?.WebApp || null;

  function fatal(message) {
    document.body.innerHTML = `
      <div style="padding:40px 20px;text-align:center;font-family:system-ui,sans-serif;">
        <div style="font-size:2.5rem;margin-bottom:16px;">⚠️</div>
        <h2 style="margin:0 0 12px;font-size:1.2rem;">Something went wrong</h2>
        <p style="color:#666;margin:0;">${String(message).replace(/</g, "&lt;")}</p>
      </div>
    `;
  }

  if (!tg) {
    fatal("This application only works inside Telegram. Please open it via the Telegram Mini App.");
    return;
  }

  try { tg.ready(); tg.expand(); } catch (_) {}

  const initData = tg.initData || "";

  if (!initData) {
    fatal("Telegram Mini App could not authenticate. Please reopen the app from Telegram.");
    return;
  }

  /* ── App State ──────────────────────────────────────── */
  const state = {
    events: [],
    filteredEvents: [],
    currentFilter: "all",
    searchTerm: "",
    activeSheet: null,
    detailEventId: null,
    editingEventId: null,
    lastFocusedElement: null,
    skip: 0,
    hasMore: false,
    isLoading: false,
    initData,
  };

  /* ── Element Refs ───────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const els = {
    syncStatus:         $("syncStatus"),
    eventCount:         $("eventCount"),
    refreshBtn:         $("refreshBtn"),
    retryBtn:           $("retryBtn"),
    emptyAddBtn:        $("emptyAddBtn"),
    onboardingOverlay:  $("onboardingOverlay"),
    onboardingIcon:     $("onboardingIcon"),
    onboardingTitle:    $("onboardingTitle"),
    onboardingText:     $("onboardingText"),
    onboardingDots:     $("onboardingDots"),
    onboardingSkipBtn:  $("onboardingSkipBtn"),
    onboardingNextBtn:  $("onboardingNextBtn"),
    repeatUntilWrap:    $("repeatUntilWrap"),
    repeatUntil:        $("repeatUntil"),
    searchInput:        $("searchInput"),
    filterButtons:      $$("[data-filter]"),
    eventsWrap:         $("eventsWrap"),
    listState:          $("listState"),
    listErrorState:     $("listErrorState"),
    noResultsState:     $("noResultsState"),
    skeletonState:      $("skeletonState"),
    loadMoreWrap:       $("loadMoreWrap"),
    loadMoreBtn:        $("loadMoreBtn"),
    toast:              $("toast"),

    // Composer
    openComposerBtn:    $("openComposerBtn"),
    closeComposerX:     $("closeComposerX"),
    cancelBtn:          $("cancelBtn"),
    saveEventBtn:       $("saveEventBtn"),
    composerSheet:      $("composerSheet"),
    composerTitle:      $("composerTitle"),
    composerSubtitle:   $("composerSubtitle"),
    eventForm:          $("eventForm"),
    eventId:            $("eventId"),
    title:              $("title"),
    date:               $("date"),
    dateJalali:         $("date-jalali"),
    repeat:             $("repeat"),
    category:           $("category"),
    pin:                $("pin"),
    note:               $("note"),
    noteCharCount:      $("noteCharCount"),
    allDay:             $("allDay"),
    eventTimeWrap:      $("eventTimeWrap"),
    eventTime:          $("eventTime"),
    reminderTimeWrap:   $("reminderTimeWrap"),
    reminderTime:       $("reminderTime"),
    reminderOffsetWrap: $("reminderOffsetWrap"),
    reminderOffset:     $("reminderOffset"),

    // Detail
    detailSheet:        $("detailSheet"),
    closeDetailX:       $("closeDetailX"),
    detailEditBtn:      $("detailEditBtn"),
    detailShareBtn:     $("detailShareBtn"),
    detailPinBtn:       $("detailPinBtn"),
    detailDeleteBtn:    $("detailDeleteBtn"),
    detailNote:         $("detailNote"),
    detailNoteSaveBtn:  $("detailNoteSaveBtn"),
    detailNoteCancelBtn:$("detailNoteCancelBtn"),
    detailEventTitle:   $("detailEventTitle"),
    detailCategoryBadge:$("detailCategoryBadge"),
    detailRepeatBadge:  $("detailRepeatBadge"),
    detailPinnedBadge:  $("detailPinnedBadge"),
    detailDateIso:      $("detailDateIso"),
    detailDateJalali:   $("detailDateJalali"),
    detailTimezone:     $("detailTimezone"),
    detailStatus:       $("detailStatus"),
    countdownRing:      $("countdownRing"),
    countdownDays:      $("countdownDays"),
    detailCountdownText:$("detailCountdownText"),

    // Confirm dialog
    confirmOverlay:     $("confirmOverlay"),
    confirmTitle:       $("confirmTitle"),
    confirmText:        $("confirmText"),
    confirmOkBtn:       $("confirmOkBtn"),
    confirmCancelBtn:   $("confirmCancelBtn"),

    sheetOverlay:       $("sheetOverlay"),
  };

  /* ── Label Maps ─────────────────────────────────────── */
  const CATEGORY_LABELS = {
    general: "🌐 General",  birthday: "🎂 Birthday",
    work:    "💼 Work",      family:   "👨‍👩‍👧 Family",
    health:  "❤️ Health",   travel:   "✈️ Travel",
    finance: "💰 Finance",  study:    "📚 Study",
    other:   "📌 Other",
  };

  /* ── Language ────────────────────────────────────────── */
  // Keyed by the English source string rather than by an invented id: the
  // template needs no data-i18n attributes, so translating it is a DOM pass
  // instead of 99 markup edits, and any string with no entry simply stays
  // English. Only Persian is offered besides English — that is deliberate, not
  // a gap: Persian serves the audience the Jalali calendar is here for, and
  // English serves everyone else.
  const TRANSLATIONS = {
    fa: {
      // Shell
      "Skip to content": "پرش به محتوا",
      "JavaScript Required": "جاوااسکریپت لازم است",
      "Please enable JavaScript to use TimeManager Pro.": "برای استفاده از تایم‌منیجر پرو جاوااسکریپت را فعال کنید.",
      "Smart reminders in Telegram": "یادآوری هوشمند در تلگرام",
      "✨ Your Personal Planner": "✨ برنامه‌ریز شخصی شما",
      "Stay on top of every moment": "هیچ لحظه‌ای را از دست ندهید",
      "Save events, birthdays & tasks — get reminders directly in Telegram.": "رویدادها، تولدها و کارها را ذخیره کنید و یادآوری‌شان را در تلگرام بگیرید.",
      "Events": "رویداد",
      "Ready": "آماده",
      "Status": "وضعیت",
      "Refresh events": "بارگذاری دوباره",
      "Refresh": "بارگذاری دوباره",

      // Filters and search
      "Filters and search": "فیلتر و جست‌وجو",
      "Category filter": "فیلتر دسته",
      "Search events…": "جست‌وجوی رویداد…",
      "Event list": "فهرست رویدادها",
      "🌐 All": "🌐 همه",
      "📌 Pinned": "📌 سنجاق‌شده",
      "🎂 Birthday": "🎂 تولد",
      "💼 Work": "💼 کاری",
      "❤️ Health": "❤️ سلامت",
      "👨‍👩‍👧 Family": "👨‍👩‍👧 خانواده",
      "✈️ Travel": "✈️ سفر",
      "💰 Finance": "💰 مالی",
      "📚 Study": "📚 درسی",
      "🗄️ Past": "🗄️ گذشته",
      "🌐 General": "🌐 عمومی",
      "📌 Other": "📌 سایر",

      // Empty, error and result states
      "No events yet!": "هنوز رویدادی ندارید!",
      "Add your first event and start receiving smart reminders directly in Telegram.": "اولین رویدادتان را اضافه کنید و یادآوری‌ها را در تلگرام دریافت کنید.",
      "Add your first event": "افزودن اولین رویداد",
      "🎂 Birthdays": "🎂 تولدها",
      "💼 Meetings": "💼 جلسه‌ها",
      "❤️ Appointments": "❤️ قرارها",
      "✈️ Travel": "✈️ سفر",
      "Something went wrong": "مشکلی پیش آمد",
      "Could not connect to the server. Please check your connection and try again.": "اتصال به سرور ممکن نشد. اینترنت را بررسی و دوباره تلاش کنید.",
      "Try again": "تلاش دوباره",
      "No results found": "چیزی پیدا نشد",
      "Try a different search term or filter.": "عبارت یا فیلتر دیگری را امتحان کنید.",
      "Load more events": "رویدادهای بیشتر",

      // Composer
      "Add event": "افزودن رویداد",
      "Add Event": "افزودن رویداد",
      "New Event": "رویداد جدید",
      "Edit Event": "ویرایش رویداد",
      "Close form": "بستن فرم",
      "Set title, date and repeat pattern.": "عنوان، تاریخ و الگوی تکرار را مشخص کنید.",
      "Event Title": "عنوان رویداد",
      "e.g. Mom's Birthday": "مثلاً تولد مامان",
      "Gregorian Date": "تاریخ میلادی",
      "Jalali Date": "تاریخ شمسی",
      "Repeat": "تکرار",
      "One time": "یک‌بار",
      "Daily": "هر روز",
      "Weekly": "هر هفته",
      "Monthly": "هر ماه",
      "Yearly": "هر سال",
      "Repeat Until": "تکرار تا",
      "(optional)": "(اختیاری)",
      "Category": "دسته",
      "All-day event": "رویداد تمام‌روز",
      "Event Time": "ساعت رویداد",
      "Reminder Time": "ساعت یادآوری",
      "Remind Me": "یادآوری",
      "At time of event": "سر ساعت رویداد",
      "15 minutes before": "۱۵ دقیقه قبل",
      "30 minutes before": "۳۰ دقیقه قبل",
      "1 hour before": "۱ ساعت قبل",
      "2 hours before": "۲ ساعت قبل",
      "1 day before": "۱ روز قبل",
      "1 week before": "۱ هفته قبل",
      "Pin this event to the top": "این رویداد بالای فهرست بماند",
      "Note": "یادداشت",
      "Add details, tasks, or a checklist…": "جزئیات، کارها یا فهرست وارسی…",
      "Cancel": "انصراف",
      "Save Event": "ذخیره رویداد",
      "Save Changes": "ذخیره تغییرات",

      // Detail sheet
      "Event Details": "جزئیات رویداد",
      "Full view and actions": "نمای کامل و عملیات",
      "Close details": "بستن جزئیات",
      "days": "روز",
      "📅 Gregorian": "📅 میلادی",
      "🗓️ Jalali": "🗓️ شمسی",
      "🌍 Timezone": "🌍 منطقه زمانی",
      "🔔 Status": "🔔 وضعیت",
      "Edit": "ویرایش",
      "Share": "اشتراک",
      "📌 Pin": "📌 سنجاق",
      "Delete": "حذف",
      "Event Note": "یادداشت رویداد",
      "Write a note, checklist, or details…": "یادداشت، فهرست وارسی یا جزئیات…",
      "Reset": "بازنشانی",
      "Save Note": "ذخیره یادداشت",
      "Delete Event?": "رویداد حذف شود؟",
      "This action cannot be undone.": "این کار قابل بازگشت نیست.",

      // Onboarding
      "Never miss what matters": "هیچ چیز مهمی را فراموش نکنید",
      "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.": "تولدها، قرارها و هر چیز دیگری را که می‌خواهید به یاد بماند اضافه کنید — تایم‌منیجر پرو به جای شما یادش می‌ماند.",
      "Reminders come straight to Telegram": "یادآوری‌ها مستقیم به تلگرام می‌رسند",
      "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.": "لازم نیست برنامه دیگری را چک کنید. سر وقتش همین‌جا پیام می‌گیرید، یک‌بار یا با تکراری که خودتان انتخاب می‌کنید.",
      "Gregorian & Jalali, together": "میلادی و شمسی، کنار هم",
      "Every date shows in both calendars automatically. Tap the + button below to add your first event.": "هر تاریخ خودکار در هر دو تقویم نشان داده می‌شود. برای افزودن اولین رویداد دکمه + را بزنید.",
      "Skip": "رد کردن",
      "Next": "بعدی",
      "Get Started": "شروع کنیم",

      // Categories and statuses rendered from JS
      "General": "عمومی",
      "Birthday": "تولد",
      "Work": "کاری",
      "Family": "خانواده",
      "Health": "سلامت",
      "Travel": "سفر",
      "Finance": "مالی",
      "Study": "درسی",
      "Other": "سایر",
      "Pinned": "سنجاق‌شده",
      "Pending": "در انتظار",
      "Processing...": "در حال ارسال…",
      "✅ Sent": "✅ ارسال شد",
      "❌ Failed": "❌ ناموفق",

      // Toasts and errors
      "Please enter an event title.": "لطفاً عنوان رویداد را وارد کنید.",
      "Please select a date.": "لطفاً تاریخ را انتخاب کنید.",
      "Please set the event time, or mark it as an all-day event.": "ساعت رویداد را مشخص کنید یا آن را تمام‌روز علامت بزنید.",
      "Event saved! You'll receive a reminder in Telegram.": "رویداد ذخیره شد! یادآوری‌اش در تلگرام می‌رسد.",
      "Event updated successfully.": "رویداد به‌روزرسانی شد.",
      "Event deleted.": "رویداد حذف شد.",
      "Note saved.": "یادداشت ذخیره شد.",
      "Event pinned to top.": "رویداد بالای فهرست سنجاق شد.",
      "Event unpinned.": "سنجاق رویداد برداشته شد.",
      "Shared!": "به اشتراک گذاشته شد!",
      "Event details copied to clipboard.": "جزئیات رویداد کپی شد.",
      "Could not share. Please try copying manually.": "اشتراک‌گذاری ممکن نشد. دستی کپی کنید.",
      "The note is too long (max 2000 chars).": "یادداشت خیلی بلند است (حداکثر ۲۰۰۰ نویسه).",
      "You have reached the maximum number of events (500).": "به حداکثر تعداد رویداد رسیده‌اید (۵۰۰).",
      "Too many requests. Please slow down.": "درخواست‌ها زیاد است. کمی آهسته‌تر.",
      "Event not found or access denied.": "رویداد پیدا نشد یا دسترسی ندارید.",
      "The request failed. Please try again.": "درخواست ناموفق بود. دوباره تلاش کنید.",
      "Telegram authentication data is incomplete.": "اطلاعات احراز هویت تلگرام ناقص است.",
      "User information was not received from Telegram.": "اطلاعات کاربر از تلگرام دریافت نشد.",
      "Authentication timestamp is invalid.": "زمان احراز هویت معتبر نیست.",
      "Server configuration error. Please contact support.": "خطای پیکربندی سرور. با پشتیبانی تماس بگیرید.",
      "Invalid event ID.": "شناسه رویداد نامعتبر است.",

      // Countdown
      "Today! 🎉": "امروز! 🎉",
      "This event is today!": "این رویداد امروز است!",
      "{days} ago": "{days} پیش",
      "This event was {days} ago": "{days} پیش بوده است",
      "{parts} remaining": "{parts} مانده",
      "{parts} left": "{parts} مانده",
      "day": "روز",
      "week": "هفته",
      "month": "ماه",
      "year": "سال",
      "yr": "سال",
      "mo": "ماه",
    },
  };

  let currentLang = "en";

  function t(text, vars) {
    const table = TRANSLATIONS[currentLang] || {};
    let out = table[text] || text;
    if (vars) {
      Object.keys(vars).forEach((key) => {
        out = out.split(`{${key}}`).join(vars[key]);
      });
    }
    return out;
  }

  // Walks the static markup once and swaps any text node or attribute whose
  // trimmed value has an entry. Unknown strings are left alone, so a missing
  // translation degrades to English rather than to a blank.
  function translateDocument(root) {
    root = root || document.body;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const parent = node.parentElement;
        if (!parent || parent.closest("script, style")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach((node) => {
      const trimmed = node.nodeValue.trim();
      const translated = t(trimmed);
      if (translated !== trimmed) node.nodeValue = node.nodeValue.replace(trimmed, translated);
    });

    ["placeholder", "aria-label", "title"].forEach((attr) => {
      root.querySelectorAll(`[${attr}]`).forEach((el) => {
        const value = (el.getAttribute(attr) || "").trim();
        const translated = t(value);
        if (translated !== value) el.setAttribute(attr, translated);
      });
    });
  }

  function applyLanguage() {
    const code = String(tg?.initDataUnsafe?.user?.language_code || "").toLowerCase();
    currentLang = code.startsWith("fa") ? "fa" : "en";

    const root = document.documentElement;
    root.lang = currentLang;
    root.dir = currentLang === "fa" ? "rtl" : "ltr";

    if (currentLang !== "en") translateDocument();
  }

  const CATEGORY_PLAIN = {
    general: "General",  birthday: "Birthday",
    work:    "Work",      family:   "Family",
    health:  "Health",   travel:   "Travel",
    finance: "Finance",  study:    "Study",
    other:   "Other",
  };

  const REPEAT_LABELS = {
    none: "One time", daily: "🔁 Daily",
    weekly: "🔁 Weekly", monthly: "🔁 Monthly", yearly: "🎂 Yearly",
  };

  const STATUS_LABELS = {
    pending: "Pending", processing: "Processing...",
    done: "✅ Sent", failed: "❌ Failed",
  };

  /* ── Telegram Theme ─────────────────────────────────── */
  // Telegram themeParams key -> CSS custom property read by style.css.
  // Every one of these has a fallback in the stylesheet, so a client that
  // sends only half of them still renders correctly.
  const TG_THEME_MAP = {
    bg_color:                "--tg-bg",
    secondary_bg_color:      "--tg-bg-2",
    section_bg_color:        "--tg-surface",
    text_color:              "--tg-text",
    subtitle_text_color:     "--tg-text-2",
    hint_color:              "--tg-text-muted",
    section_separator_color: "--tg-border",
    link_color:              "--tg-link",
    destructive_text_color:  "--tg-danger",
  };

  function initTelegram() {
    try {
      applyTelegramTheme();
      if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("secondary_bg_color");
      tg.onEvent?.("themeChanged", applyTelegramTheme);
    } catch (_) {}
  }

  function applyTelegramTheme() {
    const root = document.documentElement;
    const params = tg?.themeParams || {};

    Object.entries(TG_THEME_MAP).forEach(([key, cssVar]) => {
      const value = params[key];
      if (typeof value === "string" && value.trim()) {
        root.style.setProperty(cssVar, value.trim());
      } else {
        // Client didn't send this one — drop back to the stylesheet default
        // instead of keeping a stale value from the previous theme.
        root.style.removeProperty(cssVar);
      }
    });

    // Inside the Telegram WebView tg.colorScheme is authoritative:
    // prefers-color-scheme reports the OS setting, which can disagree with
    // the theme the user actually chose in Telegram.
    root.setAttribute("data-tg-scheme", tg?.colorScheme === "dark" ? "dark" : "light");
  }

  /* ── Loading / Status ───────────────────────────────── */
  function setLoading(on) {
    state.isLoading = on;
    document.body.classList.toggle("is-loading", on);
    if (els.syncStatus) els.syncStatus.textContent = on ? "Syncing…" : "Ready";
  }

  function setSkeleton(on) {
    if (els.skeletonState) els.skeletonState.hidden = !on;
  }

  /* ── Toast ──────────────────────────────────────────── */
  let _toastTimer = null;
  function showToast(message, type = "info") {
    if (!els.toast) return;
    els.toast.innerHTML = `
      ${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"} ${escapeHtml(message)}
    `;
    els.toast.dataset.type = type;
    els.toast.classList.add("is-visible");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => els.toast.classList.remove("is-visible"), 2800);

    // Native-feeling haptic nudge on meaningful outcomes (skip routine "info" toasts
    // so this stays purposeful rather than buzzing on everything).
    try {
      if (type === "success") tg?.HapticFeedback?.notificationOccurred?.("success");
      else if (type === "error") tg?.HapticFeedback?.notificationOccurred?.("error");
    } catch (_) {}
  }

  /* ── Custom Confirm Dialog ──────────────────────────── */
  function showConfirm({ title, text, okLabel = "Confirm", icon = "🗑️" }) {
    return new Promise((resolve) => {
      if (!els.confirmOverlay) { resolve(true); return; }

      if (els.confirmTitle) els.confirmTitle.textContent = title;
      if (els.confirmText)  els.confirmText.textContent  = text;
      if (els.confirmOkBtn) els.confirmOkBtn.textContent = okLabel;
      const iconEl = els.confirmOverlay.querySelector(".confirm-icon");
      if (iconEl) iconEl.textContent = icon;

      els.confirmOverlay.hidden = false;
      els.confirmOverlay.removeAttribute("aria-hidden");

      const cleanup = (result) => {
        els.confirmOverlay.hidden = true;
        els.confirmOverlay.setAttribute("aria-hidden", "true");
        resolve(result);
      };

      const handleOk     = () => cleanup(true);
      const handleCancel = () => cleanup(false);
      const handleKey    = (e) => { if (e.key === "Escape") cleanup(false); };

      els.confirmOkBtn?.addEventListener("click", handleOk, { once: true });
      els.confirmCancelBtn?.addEventListener("click", handleCancel, { once: true });
      document.addEventListener("keydown", handleKey, { once: true });
    });
  }

  /* ── API ────────────────────────────────────────────── */
  async function apiPost(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: state.initData, ...payload }),
    });

    let data = null;
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      const detail = data?.detail || "REQUEST_FAILED";
      throw new Error(detail);
    }
    return data;
  }

  function normalizeError(error) {
    const map = {
      NO_DATA:                "Telegram authentication data is missing.",
      BAD_HASH:               "The Telegram request signature is invalid.",
      EXPIRED:                "Your session has expired. Please reopen the Mini App.",
      INVALID_DATE:           "The date entered is not valid.",
      TITLE_TOO_LONG:         "The title is too long (max 200 chars).",
      NOTE_TOO_LONG:          "The note is too long (max 2000 chars).",
      EVENT_LIMIT_REACHED:    "You have reached the maximum number of events (500).",
      RATE_LIMIT:             "Too many requests. Please slow down.",
      NOT_FOUND_OR_UNAUTHORIZED: "Event not found or access denied.",
      REQUEST_FAILED:         "The request failed. Please try again.",
      NO_HASH:                "Telegram authentication data is incomplete.",
      NO_USER:                "User information was not received from Telegram.",
      INVALID_AUTH_DATE:      "Authentication timestamp is invalid.",
      MISCONFIGURED:          "Server configuration error. Please contact support.",
      INVALID_ID_FORMAT:      "Invalid event ID.",
    };
    const detail = error?.message || "";
    return t(map[detail]) || `Error: ${detail || "Unknown error"}`;
  }

  /* ── Load Events ─────────────────────────────────────── */
  // Long enough that a normal typing burst is one request, short enough that
  // the list still feels like it reacts as you type.
  const SEARCH_DEBOUNCE_MS = 350;

  async function loadEvents(append = false) {
    if (!append) {
      state.skip = 0;
      setSkeleton(true);
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = true;
      if (els.noResultsState) els.noResultsState.hidden = true;
      if (els.loadMoreWrap) els.loadMoreWrap.hidden = true;
    }

    setLoading(true);
    try {
      const data = await apiPost("/api/list", {
        skip:   state.skip,
        q:      state.searchTerm.trim(),
        filter: state.currentFilter,
      });
      const newItems = Array.isArray(data.targets) ? data.targets : [];
      state.hasMore = !!data.has_more;

      if (append) {
        state.events = [...state.events, ...newItems];
      } else {
        state.events = newItems;
      }

      state.skip = state.events.length;
      applyFilters();
      renderEvents();
      updateCounters();
      showStatePanel();

      if (els.loadMoreWrap) els.loadMoreWrap.hidden = !state.hasMore;
    } catch (error) {
      state.events = [];
      state.filteredEvents = [];
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = false;
      showToast(normalizeError(error), "error");
      if (els.syncStatus) els.syncStatus.textContent = "Error";
    } finally {
      setSkeleton(false);
      setLoading(false);
    }
  }

  function updateCounters() {
    if (els.eventCount) els.eventCount.textContent = String(state.events.length);
  }

  /* ── Filters ────────────────────────────────────────── */
  // The search and the filter are applied by the Mongo query behind /api/list,
  // so what comes back is already the result set. Filtering again here would
  // only ever narrow it to the current page, which is the bug this replaced.
  function applyFilters() {
    state.filteredEvents = state.events;
  }

  /* ── State Panel ────────────────────────────────────── */
  function showStatePanel() {
    const isSearching = state.searchTerm.trim() !== "" || state.currentFilter !== "all";
    const hasResults  = state.filteredEvents.length > 0;

    if (els.listState)      els.listState.hidden      = true;
    if (els.listErrorState) els.listErrorState.hidden = true;
    if (els.noResultsState) els.noResultsState.hidden = true;

    if (hasResults) return;

    // Now that the server does the filtering, an empty page means one of two
    // different things: nothing matched the query, or nothing is saved at all.
    // Comparing events to filteredEvents can no longer tell them apart.
    if (isSearching) {
      if (els.noResultsState) els.noResultsState.hidden = false;
    } else if (els.listState) {
      els.listState.hidden = false;
    }
  }

  /* ── Countdown Logic (English only) ─────────────────── */
  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addMonthsSafe(date, n) {
    const d = new Date(date.getFullYear(), date.getMonth(), 1);
    d.setMonth(d.getMonth() + n);
    const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(date.getDate(), lastDay));
    return d;
  }

  function diffParts(from, to) {
    let cursor = startOfDay(from);
    const target = startOfDay(to);
    const totalMs = target - cursor;

    if (totalMs < 0) {
      return { past: true, totalDays: Math.ceil(-totalMs / 86400000) };
    }

    const totalDays = Math.ceil(totalMs / 86400000);

    let years = 0, months = 0;
    while (addMonthsSafe(cursor, 12) <= target) { years++;  cursor = addMonthsSafe(cursor, 12); }
    while (addMonthsSafe(cursor, 1)  <= target) { months++; cursor = addMonthsSafe(cursor, 1); }

    const remDays = Math.ceil((target - cursor) / 86400000);
    const weeks = Math.floor(remDays / 7);
    const days  = remDays % 7;

    return { past: false, years, months, weeks, days, totalDays };
  }

  function pluralize(n, word) {
    // Persian marks no plural on a counted noun: "3 days" is "۳ روز", not "روزها".
    if (currentLang !== "en") return `${n} ${t(word)}`;
    return `${n} ${word}${n !== 1 ? "s" : ""}`;
  }

  function getCountdownData(dateIso) {
    if (!dateIso) return { tone: "long", shortText: "—", fullText: "—", totalDays: 999 };

    const today = startOfDay(new Date());
    const target = startOfDay(new Date(`${dateIso}T00:00:00`));
    const diff = diffParts(today, target);

    if (diff.past) {
      return {
        tone: "past",
        shortText: t("{days} ago", { days: pluralize(diff.totalDays, "day") }),
        fullText: t("This event was {days} ago", { days: pluralize(diff.totalDays, "day") }),
        totalDays: -diff.totalDays,
      };
    }

    if (diff.totalDays === 0) {
      return {
        tone: "today",
        shortText: t("Today! 🎉"),
        fullText: t("This event is today!"),
        totalDays: 0,
      };
    }

    // Build human-readable parts
    const parts = [];
    if (diff.years)  parts.push(pluralize(diff.years, "year"));
    if (diff.months) parts.push(pluralize(diff.months, "month"));
    if (diff.weeks)  parts.push(pluralize(diff.weeks, "week"));
    if (diff.days)   parts.push(pluralize(diff.days, "day"));

    const shortParts = [];
    if (diff.years)  shortParts.push(pluralize(diff.years, "yr"));
    if (diff.months) shortParts.push(pluralize(diff.months, "mo"));
    const extraDays = diff.weeks * 7 + diff.days;
    if (extraDays)   shortParts.push(pluralize(extraDays, "day"));

    const fullText  = t("{parts} remaining", { parts: parts.join("، ") });
    const shortText = t("{parts} left",       { parts: shortParts.join(" ") });

    let tone = "long";
    if      (diff.totalDays <= 3)   tone = "critical";
    else if (diff.totalDays <= 7)   tone = "critical";
    else if (diff.totalDays <= 30)  tone = "soon";
    else if (diff.totalDays <= 90)  tone = "warm";
    else if (diff.totalDays <= 180) tone = "cool";
    else if (diff.totalDays <= 365) tone = "future";

    return { tone, shortText, fullText, totalDays: diff.totalDays };
  }

  /* ── Render Events ───────────────────────────────────── */
  function renderEvents() {
    if (!els.eventsWrap) return;

    if (!state.filteredEvents.length) {
      els.eventsWrap.innerHTML = "";
      showStatePanel();
      return;
    }

    const frag = document.createDocumentFragment();

    state.filteredEvents.forEach((event) => {
      const cd = getCountdownData(event.next_date_iso || event.date_iso);
      const catClass = `cat-${event.category || "general"}`;
      const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";
      const repeatLabel = REPEAT_LABELS[event.repeat] || "One time";

      const art = document.createElement("article");
      art.className = `event-card ${catClass}`;
      art.tabIndex = 0;
      art.setAttribute("role", "button");
      art.setAttribute("aria-label", `Open details for ${event.title}`);
      art.dataset.id = event.id;

      // Progress bar: how close to event (cap at 365 days)
      const progressPct = cd.totalDays <= 0
        ? 100
        : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));

      art.innerHTML = `
        <div class="event-card-top">
          <div class="event-head">
            <h3 class="event-title">${escapeHtml(event.title)}</h3>
            <div class="event-badges">
              ${event.pinned ? '<span class="badge badge-pin">📌 Pinned</span>' : ""}
              <span class="badge ${getCatBadgeClass(event.category)}">${escapeHtml(catLabel)}</span>
              <span class="urgency-badge urgency-${cd.tone}">${escapeHtml(cd.shortText)}</span>
            </div>
          </div>
          <span class="event-repeat">${escapeHtml(repeatLabel)}</span>
        </div>

        <div class="event-progress-wrap">
          <div class="event-progress-bar">
            <div class="event-progress-fill" style="width:${progressPct}%"></div>
          </div>
          <span class="event-progress-label">${
            cd.totalDays <= 0 ? "Today!" :
            cd.totalDays === 0 ? "Today!" :
            `${cd.totalDays}d`
          }</span>
        </div>

        <div class="event-dates">
          <span>📅 ${escapeHtml(event.next_date_iso || event.date_iso || "—")}${
            event.all_day === false && event.time_hm
              ? ` · ${escapeHtml(event.time_hm)}`
              : ""
          }</span>
          <span class="event-dates-sep">•</span>
          <span>🗓️ ${escapeHtml(event.next_date_jalali || event.date_jalali || "—")}</span>
        </div>

        <div class="event-bottom">
          <span class="status-dot status-${escapeHtml(event.notify_status || "pending")}"></span>
          <span>${escapeHtml(t(STATUS_LABELS[event.notify_status] || "Pending"))}</span>
        </div>
      `;

      art.addEventListener("click",   () => openDetail(event.id));
      art.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(event.id); }
      });

      frag.appendChild(art);
    });

    els.eventsWrap.innerHTML = "";
    els.eventsWrap.appendChild(frag);
    showStatePanel();
  }

  function getCatBadgeClass(cat) {
    const map = {
      birthday: "badge-cat-birthday", work:    "badge-cat-work",
      family:   "badge-cat-family",   health:  "badge-cat-health",
      travel:   "badge-cat-travel",   finance: "badge-cat-finance",
      study:    "badge-cat-study",
    };
    return map[cat] || "";
  }

  /* ── Sheet Management ────────────────────────────────── */
  function openSheet(name, focusTgt = null) {
    state.lastFocusedElement = document.activeElement;
    if (els.sheetOverlay) els.sheetOverlay.hidden = false;

    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      const active = sheet.id === name;
      sheet.hidden = !active;
      sheet.setAttribute("aria-hidden", String(!active));
    });

    state.activeSheet = name;
    if (els.openComposerBtn) {
      els.openComposerBtn.setAttribute("aria-expanded", String(name === "composerSheet"));
    }
    updateTgBackButton();
    setTimeout(() => focusTgt?.focus?.(), 40);
  }

  function closeSheets() {
    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      sheet.hidden = true;
      sheet.setAttribute("aria-hidden", "true");
    });
    if (els.sheetOverlay) els.sheetOverlay.hidden = true;
    state.activeSheet = null;
    if (els.openComposerBtn) els.openComposerBtn.setAttribute("aria-expanded", "false");
    updateTgBackButton();
    state.lastFocusedElement?.focus?.();
  }

  function updateTgBackButton() {
    if (!tg?.BackButton) return;
    try {
      tg.BackButton.hide();
      tg.BackButton.offClick(handleTgBack);
      if (state.activeSheet) {
        tg.BackButton.onClick(handleTgBack);
        tg.BackButton.show();
      }
    } catch (_) {}
  }

  function handleTgBack() { if (state.activeSheet) closeSheets(); }

  /* ── Composer ────────────────────────────────────────── */
  // Kept in sync with the <option> values in index.html. Anything outside this
  // list (an event saved by a future build, say) falls back to one hour.
  const REMINDER_OFFSETS = [0, 15, 30, 60, 120, 1440, 10080];
  const DEFAULT_OFFSET_MINUTES = 60;

  // An all-day event has no start time, so "15 minutes before" means nothing:
  // it takes a wall-clock reminder instead. A timed event takes an offset.
  function updateAllDayVisibility() {
    const allDay = els.allDay ? els.allDay.checked : true;
    if (els.eventTimeWrap)      els.eventTimeWrap.hidden      = allDay;
    if (els.reminderTimeWrap)   els.reminderTimeWrap.hidden   = !allDay;
    if (els.reminderOffsetWrap) els.reminderOffsetWrap.hidden = allDay;
  }

  function updateRepeatUntilVisibility() {
    if (!els.repeatUntilWrap) return;
    const isRecurring = !!(els.repeat?.value && els.repeat.value !== "none");
    els.repeatUntilWrap.hidden = !isRecurring;
    if (!isRecurring && els.repeatUntil) els.repeatUntil.value = "";
    // Can't pick an end date before the event's own start date.
    if (els.repeatUntil && els.date?.value) els.repeatUntil.min = els.date.value;
  }

  function resetComposer() {
    els.eventForm?.reset();
    if (els.eventId)        els.eventId.value       = "";
    if (els.dateJalali)     els.dateJalali.value     = "";
    if (els.noteCharCount)  els.noteCharCount.textContent = "0 / 2000";
    state.editingEventId = null;
    if (els.composerTitle)    els.composerTitle.textContent    = t("New Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Set title, date and repeat pattern.";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.saveEventBtn)     els.saveEventBtn.innerHTML       = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      Save Event`;
  }

  function openCreateComposer() {
    resetComposer();
    openSheet("composerSheet", els.title);
  }

  function openEditComposer(event) {
    state.editingEventId = event.id;
    if (els.eventId)    els.eventId.value    = event.id;
    if (els.title)      els.title.value      = event.title      || "";
    if (els.date)       els.date.value       = event.date_iso   || "";
    if (els.dateJalali) els.dateJalali.value = event.date_jalali|| "";
    if (els.repeat)     els.repeat.value     = event.repeat     || "none";
    if (els.category)   els.category.value   = event.category   || "general";
    const allDay = event.all_day !== false;
    if (els.allDay)    els.allDay.checked = allDay;
    if (els.eventTime) els.eventTime.value = event.time_hm || "09:00";

    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (els.reminderTime) {
      const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9)).padStart(2, "0");
      const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0)).padStart(2, "0");
      els.reminderTime.value = `${h}:${m}`;
    }
    if (els.reminderOffset) {
      const offset = spec?.mode === "relative" ? Number(spec.offset_minutes) : DEFAULT_OFFSET_MINUTES;
      els.reminderOffset.value = String(
        REMINDER_OFFSETS.includes(offset) ? offset : DEFAULT_OFFSET_MINUTES
      );
    }
    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.pin)        els.pin.checked      = !!event.pinned;
    if (els.note)       els.note.value       = event.note       || "";
    if (els.noteCharCount) {
      els.noteCharCount.textContent = `${(event.note || "").length} / 2000`;
    }
    if (els.composerTitle)    els.composerTitle.textContent    = t("Edit Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Update the event details.";
    if (els.saveEventBtn)     els.saveEventBtn.textContent     = "Save Changes";
    openSheet("composerSheet", els.title);
  }

  /* ── Detail Panel ────────────────────────────────────── */
  function getEventById(id) {
    return state.events.find((e) => e.id === id) ?? null;
  }

  function openDetail(eventId) {
    const ev = getEventById(eventId);
    if (!ev) return;

    state.detailEventId = eventId;
    const cd = getCountdownData(ev.next_date_iso || ev.date_iso);

    // Basic fields
    if (els.detailEventTitle)    els.detailEventTitle.textContent    = ev.title || "—";
    if (els.detailCategoryBadge) {
      els.detailCategoryBadge.textContent = CATEGORY_LABELS[ev.category] || "General";
      els.detailCategoryBadge.className = `badge ${getCatBadgeClass(ev.category)}`;
    }
    if (els.detailRepeatBadge)  els.detailRepeatBadge.textContent  = REPEAT_LABELS[ev.repeat]  || "One time";
    if (els.detailPinnedBadge)  els.detailPinnedBadge.hidden        = !ev.pinned;
    if (els.detailDateIso) {
      els.detailDateIso.textContent =
        (ev.date_iso || "—") +
        (ev.all_day === false && ev.time_hm ? `  ·  ${ev.time_hm}` : "");
    }
    if (els.detailDateJalali)   els.detailDateJalali.textContent    = ev.date_jalali || "—";
    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
    if (els.detailStatus)       els.detailStatus.textContent        = t(STATUS_LABELS[ev.notify_status] || "—");
    if (els.detailNote)         els.detailNote.value                = ev.note        || "";

    // Pin button label
    if (els.detailPinBtn) {
      els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
    }

    // Countdown ring
    if (els.countdownDays) {
      els.countdownDays.textContent = cd.totalDays <= 0 ? "🎉" : String(Math.abs(cd.totalDays));
    }
    if (els.countdownRing) {
      els.countdownRing.className = `countdown-ring${
        cd.tone === "past"  ? " is-past"  :
        cd.tone === "today" ? " is-today" : ""
      }`;
    }
    if (els.detailCountdownText) {
      els.detailCountdownText.textContent = cd.fullText;
    }

    openSheet("detailSheet", els.detailNote);
  }

  /* ── Form Submit (Add / Edit) ────────────────────────── */
  async function submitEventForm(e) {
    e.preventDefault();

    const allDay = els.allDay ? els.allDay.checked : true;
    const eventTime = (els.eventTime?.value || "").trim();
    const [timeH, timeM] = (els.reminderTime?.value || "09:00").split(":");

    const reminders = allDay
      ? [{ mode: "absolute", hour: Number(timeH ?? 9), minute: Number(timeM ?? 0) }]
      : [{ mode: "relative", offset_minutes: Number(els.reminderOffset?.value ?? DEFAULT_OFFSET_MINUTES) }];

    const payload = {
      title:    els.title?.value.trim()    || "",
      date:     els.date?.value            || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      // Travels with the event so the worker, which has no initData when it
      // fires a reminder, knows which language to write it in.
      lang: currentLang,
      repeat:   els.repeat?.value          || "none",
      category: els.category?.value        || "general",
      note:     els.note?.value.trim()     || "",
      pinned:   !!els.pin?.checked,
      all_day:  allDay,
      time_hm:  allDay ? null : eventTime,
      reminders,
      // Still sent so an older server build keeps scheduling correctly.
      reminder_hour: Number(timeH ?? 9),
      reminder_minute: Number(timeM ?? 0),
      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
    };

    if (!payload.title) {
      showToast(t("Please enter an event title."), "error");
      els.title?.focus();
      return;
    }
    if (!payload.date) {
      showToast(t("Please select a date."), "error");
      els.date?.focus();
      return;
    }
    if (!allDay && !eventTime) {
      showToast(t("Please set the event time, or mark it as an all-day event."), "error");
      els.eventTime?.focus();
      return;
    }

    setLoading(true);
    try {
      if (state.editingEventId) {
        // ✅ FIX: event_id (was: eventid)
        await apiPost("/api/edit", { event_id: state.editingEventId, ...payload });
        showToast(t("Event updated successfully."), "success");
      } else {
        await apiPost("/api/add", payload);
        showToast(t("Event saved! You'll receive a reminder in Telegram."), "success");
      }
      closeSheets();
      resetComposer();
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Delete ──────────────────────────────────────────── */
  async function deleteCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    // ✅ FIX: custom confirm dialog (window.confirm broken in Telegram WebView)
    const ok = await showConfirm({
      title:   "Delete Event?",
      text:    `"${ev.title}" will be permanently removed.`,
      okLabel: "Delete",
      icon:    "🗑️",
    });
    if (!ok) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      await apiPost("/api/delete", { event_id: ev.id });
      closeSheets();
      showToast(t("Event deleted."), "success");
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Save Note ───────────────────────────────────────── */
  async function saveCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/note", {
        event_id: ev.id,
        note: els.detailNote?.value.trim() || "",
      });
      const target = getEventById(ev.id);
      if (target) target.note = data.note || "";
      showToast(t("Note saved."), "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  function resetCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev || !els.detailNote) return;
    els.detailNote.value = ev.note || "";
  }

  /* ── Pin ─────────────────────────────────────────────── */
  async function toggleCurrentPin() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const nextPinned = !ev.pinned;
    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/pin", { event_id: ev.id, pinned: nextPinned });
      ev.pinned = !!data.pinned;
      if (els.detailPinBtn)   els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
      if (els.detailPinnedBadge) els.detailPinnedBadge.hidden = !ev.pinned;
      await loadEvents();
      showToast(ev.pinned ? "Event pinned to top." : "Event unpinned.", "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Share ───────────────────────────────────────────── */
  async function shareCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const text = [
      `📅 ${ev.title}`,
      `📆 Gregorian: ${ev.date_iso}`,
      ev.all_day === false && ev.time_hm ? `🕒 Time: ${ev.time_hm}` : "",
      `🗓️ Jalali: ${ev.date_jalali}`,
      `🔄 Repeat: ${REPEAT_LABELS[ev.repeat] || "One time"}`,
      `🏷️ Category: ${t(CATEGORY_PLAIN[ev.category] || "General")}`,
      ev.note ? `📝 Note: ${ev.note}` : "",
    ].filter(Boolean).join("\n");

    try {
      if (navigator.share) {
        await navigator.share({ title: ev.title, text });
        showToast(t("Shared!"), "success");
        return;
      }
      await copyToClipboard(text);
      showToast(t("Event details copied to clipboard."), "success");
    } catch (_) {
      showToast(t("Could not share. Please try copying manually."), "error");
    }
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:absolute;left:-9999px;top:0";
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }

  /* ── Jalali / Gregorian Sync ─────────────────────────── */
  function format2(n) { return String(n).padStart(2, "0"); }

  // Julian-day based conversion (the jalaali-js algorithm). The previous pair
  // used a 33-year approximation and, worse, assigned to `gy` inside its own
  // `let` initialiser — a temporal dead zone violation that threw for every
  // year above 979, i.e. every real date. Jalali input silently never reached
  // the Gregorian field. This version was checked against the jdatetime output
  // the server produces, for all 25,567 days from 1990 to 2060: no differences
  // and no round-trip failures.
  function div(a, b) { return ~~(a / b); }
  function mod(a, b) { return a - ~~(a / b) * b; }

  const JALALI_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
                         1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];

  function jalCal(jy) {
    const bl = JALALI_BREAKS.length;
    const gy = jy + 621;
    let leapJ = -14;
    let jp = JALALI_BREAKS[0];
    if (jy < jp || jy >= JALALI_BREAKS[bl - 1]) throw new RangeError("year out of range");

    let jump = 0;
    for (let i = 1; i < bl; i += 1) {
      const jm = JALALI_BREAKS[i];
      jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }

    let n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;

    const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    const march = 20 + leapJ - leapG;

    if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
    let leap = mod(mod(n + 1, 33) - 1, 4);
    if (leap === -1) leap = 4;

    return { leap, gy, march };
  }

  function gregorianToJulian(gy, gm, gd) {
    let d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4)
          + div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
    return d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
  }

  function julianToGregorian(jdn) {
    let j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    const i = div(mod(j, 1461), 4) * 5 + 308;
    const gd = div(mod(i, 153), 5) + 1;
    const gm = mod(div(i, 153), 12) + 1;
    const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy, gm, gd };
  }

  function jalaliToGregorian(jy, jm, jd) {
    const r = jalCal(jy);
    return julianToGregorian(
      gregorianToJulian(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1
    );
  }

  function gregorianToJalali(gy, gm, gd) {
    let jy = gy - 621;
    const r = jalCal(jy);
    let k = gregorianToJulian(gy, gm, gd) - gregorianToJulian(r.gy, 3, r.march);

    if (k >= 0) {
      if (k <= 185) return { jy, jm: 1 + div(k, 31), jd: mod(k, 31) + 1 };
      k -= 186;
    } else {
      // Previous Jalali year. The leap flag is the one computed for the year we
      // started from, not for the decremented year.
      jy -= 1;
      k += 179;
      if (r.leap === 1) k += 1;
    }

    return { jy, jm: 7 + div(k, 30), jd: mod(k, 30) + 1 };
  }

  function daysInJalaliMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    // jalCal returns the number of years since the last leap year, so zero —
    // not one — is what marks a leap year. Esfand has 30 days only then.
    return jalCal(jy).leap === 0 ? 30 : 29;
  }

  function syncJalaliFromGregorian() {
    const val = els.date?.value;
    if (!val) { if (els.dateJalali) els.dateJalali.value = ""; return; }
    const [gy, gm, gd] = val.split("-").map(Number);
    if (!gy || !gm || !gd) return;
    const j = gregorianToJalali(gy, gm, gd);
    if (els.dateJalali) els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function syncGregorianFromJalali() {
    const raw = (els.dateJalali?.value || "").trim().replace(/-/g, "/");
    if (!raw) return;
    const parts = raw.split("/");
    if (parts.length !== 3) return;
    const [jy, jm, jd] = parts.map(Number);
    if (!jy || !jm || !jd) return;
    try {
      const g = jalaliToGregorian(jy, jm, jd);
      if (els.date) els.date.value = `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
    } catch (_) {
      // Out-of-range year: leave the Gregorian field as it was rather than
      // letting the error escape the change handler.
    }
  }

  /* ── Escape HTML ─────────────────────────────────────── */
  function escapeHtml(v) {
    return String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /* ── Event Bindings ──────────────────────────────────── */
  function bindEvents() {
    // Header / nav
    els.refreshBtn?.addEventListener("click", () => loadEvents());
    els.retryBtn?.addEventListener("click",   () => loadEvents());
    els.emptyAddBtn?.addEventListener("click", openCreateComposer);
    els.openComposerBtn?.addEventListener("click", openCreateComposer);
    els.closeComposerX?.addEventListener("click", closeSheets);
    els.closeDetailX?.addEventListener("click",   closeSheets);
    els.cancelBtn?.addEventListener("click",      closeSheets);
    els.sheetOverlay?.addEventListener("click",   closeSheets);

    // Form
    els.eventForm?.addEventListener("submit", submitEventForm);
    els.date?.addEventListener("change", syncJalaliFromGregorian);
    els.date?.addEventListener("change", updateRepeatUntilVisibility);
    els.repeat?.addEventListener("change", updateRepeatUntilVisibility);
    els.allDay?.addEventListener("change", updateAllDayVisibility);
    els.dateJalali?.addEventListener("change", syncGregorianFromJalali);
    els.dateJalali?.addEventListener("blur",   syncGregorianFromJalali);

    // Note char counter
    els.note?.addEventListener("input", () => {
      const len = els.note.value.length;
      if (els.noteCharCount) els.noteCharCount.textContent = `${len} / 2000`;
    });

    // Search — debounced, because every keystroke would otherwise be a round
    // trip and would burn through the per-minute budget in a few seconds.
    let searchTimer = null;
    els.searchInput?.addEventListener("input", (e) => {
      const value = e.target.value || "";
      if (value.trim() === state.searchTerm.trim()) return;

      state.searchTerm = value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadEvents(), SEARCH_DEBOUNCE_MS);
    });

    // Filters
    els.filterButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = btn.dataset.filter || "all";
        els.filterButtons.forEach((b) => b.classList.toggle("is-active", b === btn));
        if (next === state.currentFilter) return;

        state.currentFilter = next;
        clearTimeout(searchTimer);
        loadEvents();
      });
    });

    // Detail actions
    els.detailEditBtn?.addEventListener("click",        () => openEditComposer(getEventById(state.detailEventId)));
    els.detailDeleteBtn?.addEventListener("click",      deleteCurrentEvent);
    els.detailPinBtn?.addEventListener("click",         toggleCurrentPin);
    els.detailShareBtn?.addEventListener("click",       shareCurrentEvent);
    els.detailNoteSaveBtn?.addEventListener("click",    saveCurrentNote);
    els.detailNoteCancelBtn?.addEventListener("click",  resetCurrentNote);

    // Load more
    els.loadMoreBtn?.addEventListener("click", () => loadEvents(true));

    // Onboarding
    els.onboardingSkipBtn?.addEventListener("click", completeOnboarding);
    els.onboardingNextBtn?.addEventListener("click", advanceOnboarding);

    // Keyboard
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (els.confirmOverlay && !els.confirmOverlay.hidden) {
          els.confirmOverlay.hidden = true;
          return;
        }
        if (state.activeSheet) closeSheets();
      }
    });
  }

  /* ── Onboarding (first run only) ────────────────────── */
  const ONBOARDING_KEY = "tmp_onboarding_seen_v1";
  const ONBOARDING_STEPS = [
    {
      icon: "🗓️",
      title: "Never miss what matters",
      text: "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.",
    },
    {
      icon: "🔔",
      title: "Reminders come straight to Telegram",
      text: "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.",
    },
    {
      icon: "🌗",
      title: "Gregorian & Jalali, together",
      text: "Every date shows in both calendars automatically. Tap the + button below to add your first event.",
    },
  ];
  let onboardingStep = 0;

  function showOnboardingIfNeeded() {
    if (!els.onboardingOverlay) return;
    try {
      if (localStorage.getItem(ONBOARDING_KEY)) return;
    } catch (_) {
      return; // storage blocked (e.g. private mode) — don't force this on every load
    }
    onboardingStep = 0;
    renderOnboardingStep();
    els.onboardingOverlay.hidden = false;
    els.onboardingOverlay.setAttribute("aria-hidden", "false");
  }

  function renderOnboardingStep() {
    const step = ONBOARDING_STEPS[onboardingStep];
    if (els.onboardingIcon)  els.onboardingIcon.textContent  = step.icon;
    if (els.onboardingTitle) els.onboardingTitle.textContent = t(step.title);
    if (els.onboardingText)  els.onboardingText.textContent  = t(step.text);
    if (els.onboardingNextBtn) {
      els.onboardingNextBtn.textContent =
        onboardingStep === ONBOARDING_STEPS.length - 1 ? t("Get Started") : t("Next");
    }
    if (els.onboardingDots) {
      [...els.onboardingDots.children].forEach((dot, i) => {
        dot.classList.toggle("is-active", i === onboardingStep);
      });
    }
  }

  function advanceOnboarding() {
    try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    if (onboardingStep < ONBOARDING_STEPS.length - 1) {
      onboardingStep += 1;
      renderOnboardingStep();
    } else {
      completeOnboarding();
    }
  }

  function completeOnboarding() {
    if (els.onboardingOverlay) {
      els.onboardingOverlay.hidden = true;
      els.onboardingOverlay.setAttribute("aria-hidden", "true");
    }
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) {}
  }

  /* ── Boot ────────────────────────────────────────────── */
  // Before anything renders: the DOM pass rewrites the static markup, and
  // every later render reads currentLang through t().
  applyLanguage();
  initTelegram();
  bindEvents();
  loadEvents();
  showOnboardingIfNeeded();
})();
'''

CONTENT_7 = r'''/* ── Typeface ───────────────────────────────────────────
   Vazirmatn covers Latin, Persian, and both sets of digits in one family.
   That matters here because the interface mixes them constantly — "1405/01/31"
   and "14:30" sit inside Persian sentences. With two families the digits and
   the words come from different designs with different baselines, which is
   exactly the mismatch this avoids. It also halves the download and removes
   the Google Fonts round trip. */
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Regular.woff2') format('woff2');
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Medium.woff2') format('woff2');
  font-weight: 500;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-SemiBold.woff2') format('woff2');
  font-weight: 600;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Bold.woff2') format('woff2');
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}
/* 800 is used in a few headings; Bold covers it rather than shipping a
   fifth file for two rules. */
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-ExtraBold.woff2') format('woff2');
  font-weight: 800;
  font-style: normal;
  font-display: swap;
}

/* ═══════════════════════════════════════════════════════════
   TimeManager Pro — style.css v2.0
   Vibrant, mobile-first, Telegram Mini App
   ═══════════════════════════════════════════════════════════ */

[hidden],
.hidden {
  display: none !important;
}

/* ── Design Tokens ─────────────────────────────────────── */
:root {
  /* Brand */
  --brand: #5b6cf8;
  --brand-2: #8b5cf6;
  --brand-grad: linear-gradient(135deg, #5b6cf8 0%, #8b5cf6 100%);
  --brand-soft: rgba(91, 108, 248, 0.12);
  --brand-text: #ffffff;

  /* Surfaces — Telegram first, original palette as fallback */
  --bg: var(--tg-bg, #f0f2ff);
  --surface: var(--tg-surface, #ffffff);
  --surface-2: var(--tg-bg-2, #f7f8ff);
  --border: var(--tg-border, rgba(91, 108, 248, 0.13));
  --border-strong: rgba(91, 108, 248, 0.22);

  /* Text — Telegram first, original palette as fallback */
  --text: var(--tg-text, #1a1d3a);
  --text-2: var(--tg-text-2, #4a4e78);
  --text-muted: var(--tg-text-muted, #8890b8);

  /* Semantic */
  --danger: var(--tg-danger, #ef4444);
  --danger-soft: rgba(239, 68, 68, 0.1);
  --success: #10b981;
  --warning: #f59e0b;
  --link: var(--tg-link, #5b6cf8);

  /* Category Colors */
  --cat-general:  #5b6cf8;
  --cat-birthday: #ec4899;
  --cat-work:     #3b82f6;
  --cat-family:   #f97316;
  --cat-health:   #ef4444;
  --cat-travel:   #06b6d4;
  --cat-finance:  #22c55e;
  --cat-study:    #a855f7;
  --cat-other:    #78716c;

  /* Urgency Palette */
  --tone-past:     #dc2626;
  --tone-today:    #be185d;
  --tone-critical: #ea580c;
  --tone-soon:     #d97706;
  --tone-warm:     #ca8a04;
  --tone-cool:     #2563eb;
  --tone-future:   #7c3aed;
  --tone-long:     #16a34a;

  /* Shadows */
  --shadow-sm: 0 2px 12px rgba(91, 108, 248, 0.08);
  --shadow-md: 0 8px 32px rgba(91, 108, 248, 0.14);
  --shadow-lg: 0 16px 56px rgba(91, 108, 248, 0.2);
  --shadow-card: 0 2px 8px rgba(26, 29, 58, 0.06), 0 0 0 1px var(--border);

  /* Radius */
  --r-sm: 12px;
  --r-md: 18px;
  --r-lg: 24px;
  --r-xl: 32px;
  --r-pill: 999px;

  /* Spacing */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px;
  --s5: 20px; --s6: 24px; --s8: 32px;

  /* Layout */
  --safe-bottom: calc(env(safe-area-inset-bottom, 0px) + 24px);
  --app-max: 860px;
  --font: 'Vazirmatn', system-ui, -apple-system, sans-serif;
}

/* ── Dark mode ──────────────────────────────────────────
   Two triggers, one set of tokens:
     1. prefers-color-scheme  — first paint, before app.js runs
     2. [data-tg-scheme]      — set by app.js from tg.colorScheme and
        authoritative, because it wins on specificity and source order.
   Both keep the var(--tg-*) binding, so a palette supplied by Telegram
   still overrides these fallbacks. */
@media (prefers-color-scheme: dark) {
  :root {
    --bg: var(--tg-bg, #0f1024);
    --surface: var(--tg-surface, #1a1d38);
    --surface-2: var(--tg-bg-2, #1f2340);
    --border: var(--tg-border, rgba(91, 108, 248, 0.18));
    --text: var(--tg-text, #eef0ff);
    --text-2: var(--tg-text-2, #a8b0d8);
    --text-muted: var(--tg-text-muted, #5a6090);
    --shadow-card: 0 2px 8px rgba(0,0,0,0.3), 0 0 0 1px var(--border);
  }
}

:root[data-tg-scheme="dark"] {
  --bg: var(--tg-bg, #0f1024);
  --surface: var(--tg-surface, #1a1d38);
  --surface-2: var(--tg-bg-2, #1f2340);
  --border: var(--tg-border, rgba(91, 108, 248, 0.18));
  --text: var(--tg-text, #eef0ff);
  --text-2: var(--tg-text-2, #a8b0d8);
  --text-muted: var(--tg-text-muted, #5a6090);
  --shadow-card: 0 2px 8px rgba(0,0,0,0.3), 0 0 0 1px var(--border);
}

/* Telegram light while the OS is dark — undo the media query above. */
:root[data-tg-scheme="light"] {
  --bg: var(--tg-bg, #f0f2ff);
  --surface: var(--tg-surface, #ffffff);
  --surface-2: var(--tg-bg-2, #f7f8ff);
  --border: var(--tg-border, rgba(91, 108, 248, 0.13));
  --text: var(--tg-text, #1a1d3a);
  --text-2: var(--tg-text-2, #4a4e78);
  --text-muted: var(--tg-text-muted, #8890b8);
  --shadow-card: 0 2px 8px rgba(26, 29, 58, 0.06), 0 0 0 1px var(--border);
}

/* Keeps native controls (the date and time pickers) in the same scheme. */
html[data-tg-scheme="dark"]  { color-scheme: dark; }
html[data-tg-scheme="light"] { color-scheme: light; }

/* ── Reset ──────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }

html {
  color-scheme: light dark;
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
}

body {
  margin: 0;
  min-height: 100svh;
  direction: ltr;
  font-family: var(--font);
  font-size: 15px;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  text-align: start;
  overflow-x: hidden;
}

/* Ambient background gradient */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 50% at 110% -10%, rgba(91,108,248,0.15) 0%, transparent 60%),
    radial-gradient(ellipse 60% 40% at -10% 110%, rgba(139,92,246,0.1) 0%, transparent 60%);
  pointer-events: none;
  z-index: 0;
}

button, input, select, textarea { font: inherit; color: inherit; }
input, select, textarea { text-align: start; }
button { cursor: pointer; border: none; background: none; }
img, svg { max-width: 100%; display: block; }
a { color: var(--link); }

.sr-only {
  position: absolute; width: 1px; height: 1px; margin: -1px;
  border: 0; padding: 0; white-space: nowrap;
  clip-path: inset(50%); overflow: hidden;
}

/* ── Noscript ────────────────────────────────────────── */
.noscript-box {
  margin: 40px auto; max-width: 480px; padding: 32px;
  text-align: center; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--r-lg);
  position: relative; z-index: 1;
}
.noscript-icon { font-size: 2.5rem; margin-bottom: 12px; }
.noscript-sub { color: var(--text-muted); font-size: 0.88rem; }

/* ── Header ─────────────────────────────────────────── */
.app-header {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center;
  justify-content: space-between; gap: var(--s4);
  width: min(100% - 24px, var(--app-max));
  margin: 0 auto;
  padding: 16px 0 10px;
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
}

.brand { display: flex; align-items: center; gap: 12px; }

.brand-mark {
  display: grid; place-items: center;
  width: 44px; height: 44px; border-radius: 14px;
  background: var(--brand-grad);
  color: white;
  box-shadow: 0 4px 16px rgba(91, 108, 248, 0.4);
}

.brand-copy { display: flex; flex-direction: column; gap: 1px; }

.brand-title {
  font-size: 1rem; font-weight: 800;
  background: var(--brand-grad);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1.2;
}

.brand-subtitle { color: var(--text-muted); font-size: 0.78rem; font-weight: 500; }

.icon-btn {
  display: grid; place-items: center;
  width: 42px; height: 42px; border-radius: var(--r-sm);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
  box-shadow: var(--shadow-sm);
  transition: all 150ms ease;
}
.icon-btn:active { transform: scale(0.93); }

/* ── Main ────────────────────────────────────────────── */
.app-main {
  position: relative; z-index: 1;
  width: min(100% - 24px, var(--app-max));
  margin: 0 auto;
  padding: 4px 0 calc(110px + env(safe-area-inset-bottom, 0px));
  display: flex; flex-direction: column; gap: 16px;
}

/* ── Hero Card ───────────────────────────────────────── */
.hero-card {
  display: flex; align-items: center;
  justify-content: space-between; gap: 16px;
  padding: 22px;
  background: var(--brand-grad);
  border-radius: var(--r-xl);
  box-shadow: 0 8px 32px rgba(91,108,248,0.35);
  color: white;
  position: relative;
  overflow: hidden;
}

.hero-card::before {
  content: '';
  position: absolute; inset: 0;
  background: url("data:image/svg+xml,%3Csvg width='120' height='120' viewBox='0 0 120 120' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='100' cy='20' r='60' fill='rgba(255,255,255,0.05)'/%3E%3Ccircle cx='20' cy='100' r='40' fill='rgba(255,255,255,0.04)'/%3E%3C/svg%3E") no-repeat right top;
}

.hero-badge {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: var(--r-pill);
  background: rgba(255,255,255,0.18);
  font-size: 0.78rem; font-weight: 600;
  margin-bottom: 10px; backdrop-filter: blur(8px);
}

.hero-title {
  margin: 0 0 8px; font-size: clamp(1.1rem, 3vw, 1.5rem);
  font-weight: 800; line-height: 1.25; color: white;
}

.hero-text {
  margin: 0; font-size: 0.875rem; opacity: 0.85; line-height: 1.5;
}

.hero-left { flex: 1; min-width: 0; }

.hero-stats {
  display: flex; flex-direction: column; gap: 10px;
  flex-shrink: 0;
}

.stat-box {
  display: flex; flex-direction: column; align-items: center;
  padding: 12px 16px; border-radius: var(--r-md);
  background: rgba(255,255,255,0.15);
  backdrop-filter: blur(8px);
  min-width: 72px;
  text-align: center;
}

.stat-icon { font-size: 1.1rem; }
.stat-value { font-size: 1rem; font-weight: 800; color: white; line-height: 1.2; }
.stat-label { font-size: 0.72rem; opacity: 0.8; font-weight: 500; }

/* ── Toolbar ─────────────────────────────────────────── */
.toolbar { display: flex; flex-direction: column; gap: 12px; }

.toolbar-row {
  display: flex; gap: 8px;
  overflow-x: auto; padding-bottom: 4px;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.toolbar-row::-webkit-scrollbar { display: none; }

.seg-btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-2);
  padding: 8px 14px;
  border-radius: var(--r-pill);
  white-space: nowrap;
  font-size: 0.84rem; font-weight: 600;
  transition: all 150ms ease;
  flex-shrink: 0;
}

.seg-btn.is-active {
  background: var(--brand-grad);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(91,108,248,0.3);
}

.search-wrap {
  display: flex; align-items: center; gap: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 0 14px;
  min-height: 48px;
  box-shadow: var(--shadow-sm);
  transition: border-color 150ms, box-shadow 150ms;
}
.search-wrap:focus-within {
  border-color: var(--brand);
  box-shadow: 0 0 0 4px rgba(91,108,248,0.12);
}

.search-icon { color: var(--text-muted); flex-shrink: 0; }

.search-input {
  flex: 1; border: 0; outline: none;
  background: transparent;
  color: var(--text);
  font-size: 0.9rem;
}

/* ── Skeleton Loading ────────────────────────────────── */
.skeleton-list { display: flex; flex-direction: column; gap: 12px; }

.skeleton-card {
  padding: 20px; background: var(--surface);
  border-radius: var(--r-lg);
  border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px;
}

.sk-line {
  height: 14px; border-radius: var(--r-pill);
  background: linear-gradient(90deg, var(--border) 25%, rgba(91,108,248,0.06) 50%, var(--border) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.sk-title { height: 18px; width: 65%; }
.sk-sub { width: 85%; }
.sk-sub.short { width: 45%; }

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Empty / Error States ────────────────────────────── */
.empty-state {
  text-align: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: 36px 24px;
  box-shadow: var(--shadow-sm);
}

.empty-state.is-error {
  border-color: rgba(239,68,68,0.2);
}

.empty-illustration { display: flex; flex-direction: column; align-items: center; gap: 12px; margin-bottom: 20px; }

.empty-circle {
  width: 80px; height: 80px; border-radius: 50%;
  background: var(--brand-soft);
  display: grid; place-items: center;
  font-size: 2rem;
  animation: float 3s ease-in-out infinite;
}

.empty-circle.is-error-circle { background: var(--danger-soft); }

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}

.empty-dots { display: flex; gap: 6px; }
.empty-dots span {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--brand);
  animation: bounce-dot 1.4s ease-in-out infinite;
  opacity: 0.4;
}
.empty-dots span:nth-child(2) { animation-delay: 0.2s; }
.empty-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes bounce-dot {
  0%, 80%, 100% { transform: scale(1); opacity: 0.4; }
  40% { transform: scale(1.3); opacity: 1; }
}

.empty-state-title { margin: 0 0 10px; font-size: 1.2rem; font-weight: 800; }
.empty-state-text { margin: 0 0 20px; color: var(--text-muted); font-size: 0.9rem; line-height: 1.6; }

.btn-cta {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 1rem; padding: 14px 24px;
  box-shadow: 0 6px 20px rgba(91,108,248,0.35);
  margin-bottom: 16px;
}

.empty-hints {
  display: flex; gap: 8px; flex-wrap: wrap; justify-content: center;
  margin-top: 4px;
}

.hint-chip {
  padding: 6px 12px; border-radius: var(--r-pill);
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 0.8rem; font-weight: 600;
  border: 1px solid rgba(91,108,248,0.18);
}

/* ── Event Cards ─────────────────────────────────────── */
.events-wrap { display: flex; flex-direction: column; gap: 12px; }

.event-card {
  padding: 18px;
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-card);
  border-inline-start: 5px solid var(--cat-general);
  transition: transform 150ms ease, box-shadow 150ms ease;
  cursor: pointer;
  text-align: start;
  position: relative;
  overflow: hidden;
}

.event-card::before {
  content: '';
  position: absolute; inset: 0;
  opacity: 0.04;
  background: linear-gradient(135deg, var(--card-accent, var(--brand)) 0%, transparent 60%);
}

.event-card:active { transform: scale(0.985); }

/* Category accent colors */
.cat-general  { --card-accent: var(--cat-general);  border-inline-start-color: var(--cat-general); }
.cat-birthday { --card-accent: var(--cat-birthday); border-inline-start-color: var(--cat-birthday); }
.cat-work     { --card-accent: var(--cat-work);     border-inline-start-color: var(--cat-work); }
.cat-family   { --card-accent: var(--cat-family);   border-inline-start-color: var(--cat-family); }
.cat-health   { --card-accent: var(--cat-health);   border-inline-start-color: var(--cat-health); }
.cat-travel   { --card-accent: var(--cat-travel);   border-inline-start-color: var(--cat-travel); }
.cat-finance  { --card-accent: var(--cat-finance);  border-inline-start-color: var(--cat-finance); }
.cat-study    { --card-accent: var(--cat-study);    border-inline-start-color: var(--cat-study); }
.cat-other    { --card-accent: var(--cat-other);    border-inline-start-color: var(--cat-other); }

.event-card-top {
  display: flex; align-items: start;
  justify-content: space-between; gap: 12px;
}

.event-head { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }

.event-title {
  margin: 0; font-size: 1rem; font-weight: 700;
  line-height: 1.35; color: var(--text);
}

.event-badges { display: flex; gap: 6px; flex-wrap: wrap; }

.badge, .mini-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: var(--r-pill);
  font-size: 0.78rem; font-weight: 600;
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid rgba(91,108,248,0.15);
}

.badge-muted {
  background: rgba(136,144,184,0.12);
  color: var(--text-muted);
  border-color: transparent;
}

.badge-pin { background: rgba(236,72,153,0.1); color: #be185d; border-color: rgba(236,72,153,0.2); }

/* Category badge colors */
.badge-cat-birthday { background: rgba(236,72,153,0.1); color: #be185d; border-color: rgba(236,72,153,0.2); }
.badge-cat-work     { background: rgba(59,130,246,0.1); color: #1d4ed8; border-color: rgba(59,130,246,0.2); }
.badge-cat-family   { background: rgba(249,115,22,0.1); color: #c2410c; border-color: rgba(249,115,22,0.2); }
.badge-cat-health   { background: rgba(239,68,68,0.1); color: #b91c1c; border-color: rgba(239,68,68,0.2); }
.badge-cat-travel   { background: rgba(6,182,212,0.1); color: #0e7490; border-color: rgba(6,182,212,0.2); }
.badge-cat-finance  { background: rgba(34,197,94,0.1); color: #15803d; border-color: rgba(34,197,94,0.2); }
.badge-cat-study    { background: rgba(168,85,247,0.1); color: #7e22ce; border-color: rgba(168,85,247,0.2); }

/* Urgency badge */
.urgency-badge {
  display: inline-flex; align-items: center;
  padding: 3px 10px; border-radius: var(--r-pill);
  font-size: 0.77rem; font-weight: 700;
  border: none;
}

.urgency-past     { background: rgba(220,38,38,0.15); color: var(--tone-past); }
.urgency-today    { background: rgba(190,24,93,0.15); color: var(--tone-today); }
.urgency-critical { background: rgba(234,88,12,0.15); color: var(--tone-critical); }
.urgency-soon     { background: rgba(217,119,6,0.15); color: var(--tone-soon); }
.urgency-warm     { background: rgba(202,138,4,0.15); color: var(--tone-warm); }
.urgency-cool     { background: rgba(37,99,235,0.12); color: var(--tone-cool); }
.urgency-future   { background: rgba(124,58,237,0.12); color: var(--tone-future); }
.urgency-long     { background: rgba(22,163,74,0.12); color: var(--tone-long); }

.event-repeat {
  color: var(--text-muted); font-size: 0.8rem;
  white-space: nowrap; flex-shrink: 0;
  font-weight: 500;
}

/* Progress Bar (days remaining) */
.event-progress-wrap {
  margin-top: 12px;
  display: flex; align-items: center; gap: 10px;
}

.event-progress-bar {
  flex: 1; height: 5px; border-radius: var(--r-pill);
  background: var(--border);
  overflow: hidden;
}

.event-progress-fill {
  height: 100%; border-radius: var(--r-pill);
  background: var(--card-accent, var(--brand));
  transition: width 600ms ease;
}

.event-progress-label {
  font-size: 0.8rem; font-weight: 700;
  color: var(--card-accent, var(--brand));
  white-space: nowrap; flex-shrink: 0;
}

.event-dates {
  display: flex; gap: 8px; align-items: center;
  margin-top: 10px;
  color: var(--text-muted); font-size: 0.82rem;
  flex-wrap: wrap;
}

.event-dates-sep { opacity: 0.4; }

.event-bottom {
  display: flex; gap: 8px; align-items: center;
  margin-top: 10px; font-size: 0.82rem;
}

.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--text-muted); flex-shrink: 0;
}
.status-dot.status-pending    { background: var(--warning); }
.status-dot.status-processing { background: var(--brand); animation: pulse-dot 1s infinite; }
.status-dot.status-done       { background: var(--success); }
.status-dot.status-failed     { background: var(--danger); }

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ── Floating Add Button ─────────────────────────────── */
.floating-add-btn {
  position: fixed;
  inset-inline: 16px;
  bottom: var(--safe-bottom);
  z-index: 35;
  display: inline-flex; align-items: center;
  justify-content: center; gap: 10px;
  min-height: 58px; padding: 0 24px;
  border-radius: var(--r-lg);
  background: var(--brand-grad);
  color: white;
  font-size: 1rem; font-weight: 800;
  box-shadow: 0 8px 32px rgba(91,108,248,0.45);
  transition: transform 150ms ease, box-shadow 150ms ease;
  border: none;
}
.floating-add-btn:active {
  transform: scale(0.97);
  box-shadow: 0 4px 16px rgba(91,108,248,0.3);
}

/* ── Sheet Overlay ───────────────────────────────────── */
.sheet-overlay {
  position: fixed; inset: 0; z-index: 40;
  background: rgba(10, 12, 40, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

/* ── Sheet ───────────────────────────────────────────── */
.sheet {
  position: fixed; inset-inline: 0; bottom: 0; z-index: 41;
  width: min(100%, 920px);
  margin-inline: auto;
  max-height: min(90svh, 860px);
  overflow-y: auto;
  padding: 10px 20px calc(24px + env(safe-area-inset-bottom, 0px));
  background: var(--surface);
  border-radius: 28px 28px 0 0;
  box-shadow: 0 -8px 40px rgba(91,108,248,0.15);
}

.sheet-handle {
  width: 48px; height: 5px; border-radius: var(--r-pill);
  background: var(--border-strong);
  margin: 0 auto 18px;
}

.sheet-head {
  display: flex; align-items: start;
  justify-content: space-between; gap: 16px;
  margin-bottom: 20px;
}

.sheet-title { margin: 0 0 4px; font-size: 1.2rem; font-weight: 800; }
.sheet-subtitle { margin: 0; color: var(--text-muted); font-size: 0.87rem; }

/* ── Form ────────────────────────────────────────────── */
.sheet-form, .detail-card { display: flex; flex-direction: column; gap: 16px; }

.field-group { display: flex; flex-direction: column; gap: 6px; text-align: start; }

.field-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.85rem; font-weight: 700;
  color: var(--text-2);
}

.field-optional { font-weight: 400; color: var(--text-muted); margin-inline-start: 2px; }

.field-input {
  width: 100%; min-height: 50px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text);
  padding: 0 14px;
  outline: none;
  font-weight: 500;
  transition: border-color 150ms, box-shadow 150ms;
}
.field-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 4px rgba(91,108,248,0.12);
}

.field-select { cursor: pointer; appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238890b8' stroke-width='2.5' stroke-linecap='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 14px center; padding-inline-end: 36px; }

.field-textarea {
  min-height: 120px;
  padding-block: 14px;
  resize: vertical;
  line-height: 1.6;
}

.char-count {
  font-size: 0.78rem; color: var(--text-muted);
  text-align: end;
}

.grid-2 { display: grid; gap: 12px; }

.check-row {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  cursor: pointer;
  transition: border-color 150ms;
}
.check-row:hover { border-color: var(--brand); }
.check-row input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--brand); cursor: pointer; flex-shrink: 0; }
.check-label { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.check-icon { font-size: 1rem; }

/* ── Buttons ─────────────────────────────────────────── */
.form-actions, .detail-actions {
  display: grid; gap: 10px;
}

.btn-primary, .btn-secondary, .btn-danger {
  min-height: 50px; border-radius: var(--r-md);
  padding: 0 20px; font-weight: 700; font-size: 0.95rem;
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  transition: all 150ms ease;
  border: 1.5px solid transparent;
}

.btn-primary {
  background: var(--brand-grad);
  color: white;
  box-shadow: 0 4px 16px rgba(91,108,248,0.3);
}
.btn-primary:active { transform: scale(0.97); box-shadow: none; }

.btn-secondary {
  background: var(--surface-2);
  color: var(--text-2);
  border-color: var(--border);
}
.btn-secondary:active { transform: scale(0.97); }

.btn-danger {
  background: rgba(239,68,68,0.1);
  color: var(--danger);
  border-color: rgba(239,68,68,0.25);
}
.btn-danger:active { transform: scale(0.97); }

/* Action buttons in detail */
.detail-actions {
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.btn-action {
  min-height: 44px; border-radius: var(--r-md);
  padding: 0 12px; font-weight: 700; font-size: 0.83rem;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  transition: all 150ms ease; border: 1.5px solid var(--border);
}

.btn-action-edit   { background: rgba(91,108,248,0.08); color: var(--brand); border-color: rgba(91,108,248,0.2); }
.btn-action-share  { background: rgba(16,185,129,0.08); color: #059669; border-color: rgba(16,185,129,0.2); }
.btn-action-pin    { background: rgba(236,72,153,0.08); color: #be185d; border-color: rgba(236,72,153,0.2); }
.btn-action-delete { background: rgba(239,68,68,0.08); color: var(--danger); border-color: rgba(239,68,68,0.2); }
.btn-action:active { transform: scale(0.96); }

/* ── Detail Card ─────────────────────────────────────── */
.detail-topline { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-event-title { margin: 8px 0; font-size: 1.3rem; font-weight: 800; line-height: 1.3; }

/* Countdown Ring */
.countdown-ring-wrap {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 24px; margin: 4px 0;
  background: var(--brand-soft);
  border-radius: var(--r-xl);
  border: 1.5px solid rgba(91,108,248,0.15);
}

.countdown-ring {
  width: 90px; height: 90px; border-radius: 50%;
  background: var(--brand-grad);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  box-shadow: 0 8px 24px rgba(91,108,248,0.4);
  color: white;
}

.ring-days { font-size: 1.6rem; font-weight: 800; line-height: 1; }
.ring-label { font-size: 0.7rem; font-weight: 600; opacity: 0.85; }

.countdown-ring-text {
  font-size: 0.92rem; font-weight: 700;
  color: var(--brand); text-align: center;
}

/* Past event ring */
.countdown-ring.is-past { background: linear-gradient(135deg, #dc2626, #b91c1c); }
.countdown-ring.is-today { background: linear-gradient(135deg, #be185d, #9d174d); }

.detail-meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
}

.detail-meta-box {
  padding: 12px 14px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
}

.detail-meta-label {
  display: block; color: var(--text-muted);
  font-size: 0.78rem; font-weight: 600; margin-bottom: 4px;
}

/* ── Custom Confirm Dialog ───────────────────────────── */
.confirm-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(10, 12, 40, 0.6);
  backdrop-filter: blur(8px);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}

.confirm-dialog {
  background: var(--surface);
  border-radius: var(--r-xl);
  padding: 32px 24px 24px;
  max-width: 320px; width: 100%;
  text-align: center;
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--border);
}

.confirm-icon { font-size: 2.5rem; margin-bottom: 12px; }
.confirm-title { margin: 0 0 8px; font-size: 1.1rem; font-weight: 800; }
.confirm-text { margin: 0 0 24px; color: var(--text-muted); font-size: 0.88rem; }
.confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

/* ── Onboarding ──────────────────────────────────────── */
.onboarding-dialog { max-width: 340px; padding-top: 36px; }

.onboarding-dots { display: flex; justify-content: center; gap: 7px; margin-bottom: 24px; }
.onboarding-dots span {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--border-strong);
  transition: background 200ms, width 200ms;
}
.onboarding-dots span.is-active { width: 20px; border-radius: var(--r-pill); background: var(--brand); }

/* ── Toast ───────────────────────────────────────────── */
.toast {
  position: fixed;
  inset-inline: 16px;
  bottom: calc(var(--safe-bottom) + 80px);
  z-index: 70;
  padding: 14px 18px;
  border-radius: var(--r-md);
  background: var(--text);
  color: white;
  font-weight: 600; font-size: 0.9rem;
  box-shadow: var(--shadow-lg);
  opacity: 0; pointer-events: none;
  transform: translateY(12px);
  transition: opacity 200ms ease, transform 200ms ease;
  display: flex; align-items: center; gap: 10px;
}

.toast.is-visible { opacity: 1; transform: translateY(0); }
.toast[data-type="success"] { background: #0d7a4a; }
.toast[data-type="error"] { background: #b91c1c; }

/* ── Load More ───────────────────────────────────────── */
.load-more-wrap { display: flex; justify-content: center; padding: 8px 0; }

.btn-load-more {
  padding: 12px 28px; border-radius: var(--r-pill);
  font-size: 0.88rem; min-height: 44px;
}

/* ── Loading State ───────────────────────────────────── */
.is-loading .floating-add-btn,
.is-loading .icon-btn,
.is-loading .btn-primary,
.is-loading .btn-secondary,
.is-loading .btn-danger,
.is-loading .btn-action {
  opacity: 0.65; pointer-events: none;
}

/* ── Responsive ──────────────────────────────────────── */
@media (min-width: 560px) {
  .grid-2 { grid-template-columns: repeat(2, 1fr); }
  .detail-actions { grid-template-columns: repeat(4, 1fr); }
  .form-actions { grid-template-columns: 1fr 1.5fr; }
}

@media (min-width: 720px) {
  .hero-card { padding: 28px 32px; }
  .hero-stats { flex-direction: row; }
  .stat-box { flex-direction: row; gap: 10px; align-items: center; }

  .sheet {
    left: 50%; right: auto; width: min(92%, 680px);
    transform: translateX(-50%);
    bottom: 20px; border-radius: 28px;
  }
}

/* ── Accessibility ───────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

/* ── Right-to-left ──────────────────────────────────────
   The layout is flexbox and grid throughout, so dir="rtl" on <html> mirrors
   almost everything on its own. These are the few places that needed a hand:
   the select chevron is a background image, which has no logical equivalent,
   and a handful of glyphs read better mirrored. */
[dir="rtl"] .field-select {
  background-position: left 14px center;
  padding-inline-end: 14px;
  padding-inline-start: 36px;
}

[dir="rtl"] .back-btn svg,
[dir="rtl"] .chevron,
[dir="rtl"] .arrow-icon {
  transform: scaleX(-1);
}
'''

CONTENT_8 = r'''<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#5b6cf8">
  <meta name="color-scheme" content="light dark">
  <meta name="description" content="TimeManager Pro — Smart event reminders with Gregorian and Jalali dates for Telegram.">
  <title>TimeManager Pro</title>
  <!-- Self-hosted: Google Fonts is slow or unreachable for a large part of this
       app's audience, and Plus Jakarta Sans carries no Persian glyphs at all,
       so every Persian word fell back to whatever the device happened to have. -->
  <link rel="preload" href="/static/fonts/Vazirmatn-Regular.woff2?v={{ asset_version }}" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/static/style.css?v={{ asset_version }}">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
</head>
<body>
  <a href="#eventList" class="sr-only">Skip to content</a>

  <noscript>
    <div class="noscript-box">
      <div class="noscript-icon">📅</div>
      <p><strong>JavaScript Required</strong></p>
      <p class="noscript-sub">Please enable JavaScript to use TimeManager Pro.</p>
    </div>
  </noscript>

  <!-- ── Header ─────────────────────────────────────── -->
  <header class="app-header">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="4" width="18" height="17" rx="3" stroke="currentColor" stroke-width="2"/>
          <path d="M3 9h18" stroke="currentColor" stroke-width="2"/>
          <path d="M8 2v4M16 2v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <circle cx="12" cy="15" r="2" fill="currentColor"/>
        </svg>
      </div>
      <div class="brand-copy">
        <strong class="brand-title">TimeManager Pro</strong>
        <span class="brand-subtitle">Smart reminders in Telegram</span>
      </div>
    </div>
    <button type="button" id="refreshBtn" class="icon-btn" aria-label="Refresh events" title="Refresh">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
      </svg>
    </button>
  </header>

  <!-- ── Main Content ───────────────────────────────── -->
  <main id="eventList" class="app-main">

    <!-- Hero Card -->
    <section class="hero-card">
      <div class="hero-left">
        <div class="hero-badge">✨ Your Personal Planner</div>
        <h1 class="hero-title">Stay on top of every moment</h1>
        <p class="hero-text">Save events, birthdays & tasks — get reminders directly in Telegram.</p>
      </div>
      <div class="hero-stats">
        <div class="stat-box">
          <span class="stat-icon">📅</span>
          <strong id="eventCount" class="stat-value">0</strong>
          <span class="stat-label">Events</span>
        </div>
        <div class="stat-box">
          <span class="stat-icon">🔔</span>
          <strong id="syncStatus" class="stat-value">Ready</strong>
          <span class="stat-label">Status</span>
        </div>
      </div>
    </section>

    <!-- Toolbar -->
    <section class="toolbar" aria-label="Filters and search">
      <div class="toolbar-row" id="filterRow" role="group" aria-label="Category filter">
        <button type="button" class="seg-btn is-active" data-filter="all">🌐 All</button>
        <button type="button" class="seg-btn" data-filter="pinned">📌 Pinned</button>
        <button type="button" class="seg-btn" data-filter="birthday">🎂 Birthday</button>
        <button type="button" class="seg-btn" data-filter="work">💼 Work</button>
        <button type="button" class="seg-btn" data-filter="health">❤️ Health</button>
        <button type="button" class="seg-btn" data-filter="family">👨‍👩‍👧 Family</button>
        <button type="button" class="seg-btn" data-filter="travel">✈️ Travel</button>
        <button type="button" class="seg-btn" data-filter="finance">💰 Finance</button>
        <button type="button" class="seg-btn" data-filter="study">📚 Study</button>
        <button type="button" class="seg-btn" data-filter="past">🗄️ Past</button>
      </div>
      <label class="search-wrap" for="searchInput">
        <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input type="search" id="searchInput" class="search-input" placeholder="Search events…" autocomplete="off" inputmode="search">
      </label>
    </section>

    <!-- Skeleton (hidden by default, shown while loading) -->
    <section id="skeletonState" class="skeleton-list" hidden>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div></div>
    </section>

    <!-- Empty State -->
    <section id="listState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle">
          <span class="empty-emoji">🗓️</span>
        </div>
        <div class="empty-dots">
          <span></span><span></span><span></span>
        </div>
      </div>
      <h2 class="empty-state-title">No events yet!</h2>
      <p class="empty-state-text">Add your first event and start receiving smart reminders directly in Telegram.</p>
      <button type="button" id="emptyAddBtn" class="btn-primary btn-cta">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        Add your first event
      </button>
      <div class="empty-hints">
        <div class="hint-chip">🎂 Birthdays</div>
        <div class="hint-chip">💼 Meetings</div>
        <div class="hint-chip">❤️ Appointments</div>
        <div class="hint-chip">✈️ Travel</div>
      </div>
    </section>

    <!-- Error State -->
    <section id="listErrorState" class="empty-state is-error" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle is-error-circle"><span class="empty-emoji">⚠️</span></div>
      </div>
      <h2 class="empty-state-title">Something went wrong</h2>
      <p class="empty-state-text">Could not connect to the server. Please check your connection and try again.</p>
      <button type="button" id="retryBtn" class="btn-primary">Try again</button>
    </section>

    <!-- No Results State -->
    <section id="noResultsState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle"><span class="empty-emoji">🔍</span></div>
      </div>
      <h2 class="empty-state-title">No results found</h2>
      <p class="empty-state-text">Try a different search term or filter.</p>
    </section>

    <!-- Events List -->
    <section id="eventsWrap" class="events-wrap" aria-live="polite" aria-label="Event list"></section>

    <!-- Load More -->
    <div id="loadMoreWrap" class="load-more-wrap" hidden>
      <button type="button" id="loadMoreBtn" class="btn-secondary btn-load-more">Load more events</button>
    </div>
  </main>

  <!-- ── Floating Add Button ────────────────────────── -->
  <button type="button" id="openComposerBtn" class="floating-add-btn" aria-label="Add event" aria-controls="composerSheet" aria-expanded="false">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
    <span>Add Event</span>
  </button>

  <!-- ── Sheet Overlay ──────────────────────────────── -->
  <div id="sheetOverlay" class="sheet-overlay" hidden aria-hidden="true"></div>

  <!-- ── Composer Sheet ─────────────────────────────── -->
  <section id="composerSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="composerTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="composerTitle" class="sheet-title">New Event</h2>
        <p id="composerSubtitle" class="sheet-subtitle">Set title, date and repeat pattern.</p>
      </div>
      <button type="button" id="closeComposerX" class="icon-btn" aria-label="Close form">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <form id="eventForm" class="sheet-form" novalidate>
      <input type="hidden" id="eventId">

      <div class="field-group">
        <label class="field-label" for="title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Event Title
        </label>
        <input type="text" id="title" class="field-input" placeholder="e.g. Mom's Birthday" maxlength="200" autocomplete="off" required>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="date">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            Gregorian Date
          </label>
          <input type="date" id="date" class="field-input" required>
        </div>
        <div class="field-group">
          <label class="field-label" for="date-jalali">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
            Jalali Date
          </label>
          <input type="text" id="date-jalali" class="field-input" placeholder="1405/01/31" maxlength="10" inputmode="numeric" autocomplete="off" dir="ltr">
        </div>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="repeat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
            Repeat
          </label>
          <select id="repeat" class="field-input field-select">
            <option value="none">One time</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="field-group" id="repeatUntilWrap" hidden>
          <label class="field-label" for="repeatUntil">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
            Repeat Until
            <span class="field-optional">(optional)</span>
          </label>
          <input type="date" id="repeatUntil" class="field-input">
        </div>
        <div class="field-group">
          <label class="field-label" for="category">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
            Category
          </label>
          <select id="category" class="field-input field-select">
            <option value="general">🌐 General</option>
            <option value="birthday">🎂 Birthday</option>
            <option value="work">💼 Work</option>
            <option value="family">👨‍👩‍👧 Family</option>
            <option value="health">❤️ Health</option>
            <option value="travel">✈️ Travel</option>
            <option value="finance">💰 Finance</option>
            <option value="study">📚 Study</option>
            <option value="other">📌 Other</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="allDay">
        <input type="checkbox" id="allDay" checked>
        <span class="check-label">
          <span class="check-icon">📆</span>
          All-day event
        </span>
      </label>

      <div class="grid-2">
        <div class="field-group" id="eventTimeWrap" hidden>
          <label class="field-label" for="eventTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            Event Time
          </label>
          <input type="time" id="eventTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderTimeWrap">
          <label class="field-label" for="reminderTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Reminder Time
          </label>
          <input type="time" id="reminderTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderOffsetWrap" hidden>
          <label class="field-label" for="reminderOffset">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Remind Me
          </label>
          <select id="reminderOffset" class="field-input field-select">
            <option value="0">At time of event</option>
            <option value="15">15 minutes before</option>
            <option value="30">30 minutes before</option>
            <option value="60" selected>1 hour before</option>
            <option value="120">2 hours before</option>
            <option value="1440">1 day before</option>
            <option value="10080">1 week before</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="pin">
        <input type="checkbox" id="pin">
        <span class="check-label">
          <span class="check-icon">📌</span>
          Pin this event to the top
        </span>
      </label>

      <div class="field-group">
        <label class="field-label" for="note">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
          Note <span class="field-optional">(optional)</span>
        </label>
        <textarea id="note" class="field-input field-textarea" rows="4" maxlength="2000" placeholder="Add details, tasks, or a checklist…"></textarea>
        <span class="char-count" id="noteCharCount">0 / 2000</span>
      </div>

      <div class="form-actions">
        <button type="button" id="cancelBtn" class="btn-secondary">Cancel</button>
        <button type="submit" id="saveEventBtn" class="btn-primary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Save Event
        </button>
      </div>
    </form>
  </section>

  <!-- ── Detail Sheet ───────────────────────────────── -->
  <section id="detailSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="detailTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="detailTitle" class="sheet-title">Event Details</h2>
        <p id="detailSubtitle" class="sheet-subtitle">Full view and actions</p>
      </div>
      <button type="button" id="closeDetailX" class="icon-btn" aria-label="Close details">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <article class="detail-card">
      <div class="detail-topline">
        <span id="detailCategoryBadge" class="badge">General</span>
        <span id="detailRepeatBadge" class="badge badge-muted">One time</span>
        <span id="detailPinnedBadge" class="badge badge-pin" hidden>📌 Pinned</span>
      </div>

      <h3 id="detailEventTitle" class="detail-event-title">—</h3>

      <!-- Countdown Ring -->
      <div class="countdown-ring-wrap" id="countdownRingWrap">
        <div class="countdown-ring" id="countdownRing">
          <span id="countdownDays" class="ring-days">—</span>
          <span class="ring-label">days</span>
        </div>
        <div id="detailCountdownText" class="countdown-ring-text">—</div>
      </div>

      <div class="detail-meta-grid">
        <div class="detail-meta-box">
          <span class="detail-meta-label">📅 Gregorian</span>
          <strong id="detailDateIso">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🗓️ Jalali</span>
          <strong id="detailDateJalali">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🌍 Timezone</span>
          <strong id="detailTimezone">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🔔 Status</span>
          <strong id="detailStatus">—</strong>
        </div>
      </div>

      <div class="detail-actions">
        <button type="button" id="detailEditBtn" class="btn-action btn-action-edit">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Edit
        </button>
        <button type="button" id="detailShareBtn" class="btn-action btn-action-share">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
          Share
        </button>
        <button type="button" id="detailPinBtn" class="btn-action btn-action-pin">📌 Pin</button>
        <button type="button" id="detailDeleteBtn" class="btn-action btn-action-delete">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          Delete
        </button>
      </div>

      <div class="field-group">
        <label class="field-label" for="detailNote">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          Event Note
        </label>
        <textarea id="detailNote" class="field-input field-textarea" rows="6" maxlength="2000" placeholder="Write a note, checklist, or details…"></textarea>
      </div>

      <div class="form-actions">
        <button type="button" id="detailNoteCancelBtn" class="btn-secondary">Reset</button>
        <button type="button" id="detailNoteSaveBtn" class="btn-primary">Save Note</button>
      </div>
    </article>
  </section>

  <!-- ── First-run Onboarding ──────────────────────── -->
  <div id="onboardingOverlay" class="confirm-overlay onboarding-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">
      <div class="confirm-icon" id="onboardingIcon">🗓️</div>
      <h3 id="onboardingTitle" class="confirm-title">Never miss what matters</h3>
      <p id="onboardingText" class="confirm-text">Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.</p>
      <div class="onboarding-dots" id="onboardingDots" aria-hidden="true">
        <span class="is-active"></span><span></span><span></span>
      </div>
      <div class="confirm-actions onboarding-actions">
        <button type="button" id="onboardingSkipBtn" class="btn-secondary">Skip</button>
        <button type="button" id="onboardingNextBtn" class="btn-primary">Next</button>
      </div>
    </div>
  </div>

  <!-- ── Custom Confirm Dialog ──────────────────────── -->
  <div id="confirmOverlay" class="confirm-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle">
      <div class="confirm-icon" id="confirmIcon">🗑️</div>
      <h3 id="confirmTitle" class="confirm-title">Delete Event?</h3>
      <p id="confirmText" class="confirm-text">This action cannot be undone.</p>
      <div class="confirm-actions">
        <button type="button" id="confirmCancelBtn" class="btn-secondary">Cancel</button>
        <button type="button" id="confirmOkBtn" class="btn-danger">Delete</button>
      </div>
    </div>
  </div>

  <!-- ── Toast ─────────────────────────────────────── -->
  <div id="toast" class="toast" role="status" aria-live="polite" aria-atomic="true"></div>

  <script src="/static/app.js?v={{ asset_version }}" defer></script>
</body>
</html>
'''

CONTENT_9 = r'''from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
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

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
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

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
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

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
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


def test_add_request_accepts_the_exact_payload_the_mini_app_sends() -> None:
    """The request models forbid unknown keys, so a field added in app.js and
    forgotten here would 422 in production while every unit test still passed.
    These two dicts are the payloads submitEventForm builds, key for key."""
    from app.schemas.requests import AddEventRequest
    from app.services.events import _normalize_event_input

    all_day_payload = {
        "initData": "dummy",
        "title": "Mom birthday",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "yearly",
        "category": "birthday",
        "note": "",
        "pinned": False,
        "all_day": True,
        "time_hm": None,
        "reminders": [{"mode": "absolute", "hour": 7, "minute": 30}],
        "reminder_hour": 7,
        "reminder_minute": 30,
        "repeat_until": None,
    }
    timed_payload = {
        **all_day_payload,
        "title": "Team standup",
        "repeat": "daily",
        "category": "work",
        "all_day": False,
        "time_hm": "14:30",
        "reminders": [{"mode": "relative", "offset_minutes": 60}],
    }

    all_day_doc = _normalize_event_input(AddEventRequest(**all_day_payload))
    assert all_day_doc["time_hm"] is None
    assert all_day_doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]

    timed_doc = _normalize_event_input(AddEventRequest(**timed_payload))
    assert timed_doc["time_hm"] == "14:30"
    assert timed_doc["event_ts_utc"] - timed_doc["next_notify_at"] == timedelta(minutes=60)


def test_next_occurrence_is_the_series_start_for_a_one_off_event() -> None:
    from app.services.events import next_occurrence_iso

    doc = {
        "date_iso": "2026-04-20",
        "tz_name": "Europe/Berlin",
        "event_ts_utc": datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc),
    }
    assert next_occurrence_iso(doc) == "2026-04-20"


def test_next_occurrence_follows_the_advanced_timestamp() -> None:
    """A yearly birthday from 2020 counts down to the next one, not to 2020 —
    the worker moves event_ts_utc forward while date_iso stays put."""
    from app.services.events import next_occurrence_iso, serialize_event

    doc = {
        "_id": "x",
        "title": "Mom birthday",
        "date_iso": "2020-04-20",
        "date_jalali": "1399/02/01",
        "repeat": "yearly",
        "notify_status": "pending",
        "tz_name": "Europe/Berlin",
        "category": "birthday",
        "pinned": False,
        "note": "",
        "event_ts_utc": datetime(2027, 4, 19, 22, 0, tzinfo=timezone.utc),
    }

    assert next_occurrence_iso(doc) == "2027-04-20"

    out = serialize_event(doc)
    assert out.date_iso == "2020-04-20"
    assert out.next_date_iso == "2027-04-20"
    assert out.next_date_jalali != out.date_jalali


def test_next_occurrence_falls_back_when_the_timestamp_is_missing() -> None:
    from app.services.events import next_occurrence_iso

    assert next_occurrence_iso({"date_iso": "2026-04-20"}) == "2026-04-20"


def test_one_off_events_are_not_given_a_ttl() -> None:
    """expire_at is what the TTL index deletes on. A one-off event keeps no
    expiry, so the archive survives; a series still gets its safety net."""
    from app.services.events import _normalize_event_input

    once = _normalize_event_input(_event_payload(repeat="none"))
    series = _normalize_event_input(_event_payload(repeat="yearly"))

    assert "expire_at" not in once
    assert "expire_at" in series
'''

CONTENT_10 = r'''from __future__ import annotations

import pytest

from app.schemas.requests import ListEventsRequest
from app.services.events import build_list_query
from app.utils.text import normalize_digits, regex_clause, search_variants


def _request(**overrides) -> ListEventsRequest:
    return ListEventsRequest(initData="dummy", **overrides)


# ── Term handling ───────────────────────────────────────────────────────────

def test_normalize_digits_maps_persian_and_arabic_forms() -> None:
    assert normalize_digits("۱۴۰۵/۰۱/۳۱") == "1405/01/31"
    assert normalize_digits("٢٠٢٦") == "2026"
    assert normalize_digits("plain") == "plain"


def test_search_variants_returns_both_spellings_only_when_they_differ() -> None:
    assert search_variants("۳") == ["۳", "3"]
    assert search_variants("meeting") == ["meeting"]
    assert search_variants("   ") == []


def test_regex_clause_escapes_the_term() -> None:
    """An unescaped term is a query-injection surface and a way to hand the
    database a pathological pattern."""
    clause = regex_clause("title", "a.*b(")
    assert clause["title"]["$regex"] == r"a\.\*b\("
    assert clause["title"]["$options"] == "i"


def test_regex_clause_can_be_case_sensitive() -> None:
    assert "$options" not in regex_clause("date_iso", "2026", case_insensitive=False)["date_iso"]


# ── Query building ──────────────────────────────────────────────────────────

ARCHIVED = {"repeat": "none", "notify_status": "done", "pinned": {"$ne": True}}


def test_plain_list_query_is_scoped_to_the_user() -> None:
    query = build_list_query("42", _request())
    assert query["user_id"] == "42"
    assert "$or" not in query


def test_default_list_hides_archived_events() -> None:
    """A one-off event whose reminder has fired is kept, but out of the way."""
    assert build_list_query("42", _request())["$nor"] == [ARCHIVED]


def test_pinned_archived_events_stay_in_the_main_list() -> None:
    """Pinning is the request to keep something in view, archived or not."""
    assert ARCHIVED["pinned"] == {"$ne": True}


def test_past_filter_shows_only_archived_events() -> None:
    query = build_list_query("42", _request(filter="past"))
    assert query["repeat"] == "none"
    assert query["notify_status"] == "done"
    assert "$nor" not in query


def test_pinned_filter() -> None:
    assert build_list_query("42", _request(filter="pinned"))["pinned"] is True


def test_category_filter() -> None:
    assert build_list_query("42", _request(filter="birthday"))["category"] == "birthday"


def test_search_adds_an_or_over_the_text_fields() -> None:
    query = build_list_query("42", _request(q="mom"))
    fields = {key for clause in query["$or"] for key in clause}

    assert query["user_id"] == "42"
    assert {"title", "note", "date_iso", "date_jalali"} <= fields


def test_search_matches_category_names_too() -> None:
    """The old client-side search matched the category label; keep that."""
    query = build_list_query("42", _request(q="birth"))
    categories = [c["category"]["$in"] for c in query["$or"] if "category" in c]

    assert categories == [["birthday"]]


def test_search_covers_both_digit_spellings() -> None:
    query = build_list_query("42", _request(q="۱۴۰۵"))
    patterns = {
        clause["date_jalali"]["$regex"]
        for clause in query["$or"]
        if "date_jalali" in clause
    }

    assert patterns == {"۱۴۰۵", "1405"}


def test_search_and_filter_combine() -> None:
    query = build_list_query("42", _request(q="mom", filter="pinned"))
    assert query["pinned"] is True
    assert query["$or"]
    # $or and $nor are separate top-level keys, so they AND together.
    assert query["$nor"] == [ARCHIVED]


def test_list_request_rejects_an_unknown_filter() -> None:
    with pytest.raises(ValueError):
        _request(filter="'; drop everything")
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "app/db.py",
        "5548e30dac42dc237ed269c9688e9eb5edfcd38a04f0ecd4b103c9cd4ba79432",
        "b2d89464c583a992b1abbf2c5dbe3f38a0b71d9613c4aadd63509d06abe8ca14",
        CONTENT_1,
    ),
    (
        "app/main.py",
        "e7049f739e5bbb4f62f313eefab4f75e7b8404f3eaa66cfeef29f71f1cd7d469",
        "1dc9aa92af2a5e9beb220e628da9e91e18369e8bf9c3ee390069ee35aa6e1b3c",
        CONTENT_2,
    ),
    (
        "app/schemas/requests.py",
        "fc97ef07cde00150921e3a4f8e087b4a09aaf10ebca06de73bec5d27ee02854c",
        "7a7561c5fc5e52f56e3c57d42207a1135f07a6a21275083d99e070a19b8452eb",
        CONTENT_3,
    ),
    (
        "app/schemas/responses.py",
        "a2a9c975c24f4f83cd1d3dc20e02218efd2c18ea0a778e160cc71133cff456b1",
        "9aaff46188bc24a095d21e21dc3c6b8084cde577b6baa96fb1218cecb80fbdad",
        CONTENT_4,
    ),
    (
        "app/services/events.py",
        "6e7bac7c5d07e21c8a9cbf82dd840bc69f9fb74565e75031b084b3c77be22cf6",
        "a6ed585ed98d2bf28f1ff2b0c53763880ea07a2815d66384eede4a49d827738e",
        CONTENT_5,
    ),
    (
        "static/app.js",
        "e7f9bb77a8fdac549406485d016c5211b7a862d65c1906bbfca9dec756f823ce",
        "17dcf2780fc2a509b95ae5bca2ec427fcc43fe15b457fe92711a045556f5ac55",
        CONTENT_6,
    ),
    (
        "static/style.css",
        "04ecd15e69d1fb96a41b338bc859107a06d795e144aa1efc6c78884af4e2f35a",
        "026ed3f7784d895403fb0db738840ac6880167e8f5dc54b7b23744aafcaa933a",
        CONTENT_7,
    ),
    (
        "templates/index.html",
        "5ff465d5a07357581361dfeea2af2a40eafe0aaf5db92bb3cdf210dfa498c18f",
        "6a7a837d6b75f5dcb3b6bf4a9530103b06d3caa8b9bb24262494daf7de38dc22",
        CONTENT_8,
    ),
    (
        "tests/test_events_api.py",
        "b8a4e3ff1d56c07f763d6e4e6731643d0dd72966e9cff2aa6eb19dd3a33a914e",
        "55f224eade478c6a690283ebeb6f57879dd42168c091972290ca4c307ef9c66c",
        CONTENT_9,
    ),
    (
        "tests/test_search.py",
        "184d3b1a0f0d4d8ff1d4ae35cf5f14e6a9adeae63745a14ab39f05231a756d21",
        "5fa7eaa6e4395e5a2b1822e7a0d46bbb47fc54705c8fb47460be00db6f32e8d0",
        CONTENT_10,
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


def install_fonts() -> None:
    if all((FONT_DIR / name).exists()
           and sha((FONT_DIR / name).read_bytes()) == digest
           for name, digest in FONTS.items()):
        log("skipped", "fonts (already installed)")
        return

    if shutil.which("npm") is None:
        log("MANUAL", "fonts — npm not found; install it and re-run for the fonts only")
        return

    FONT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = subprocess.run(
                ["npm", "pack", FONT_PACKAGE, "--silent"],
                cwd=tmp, capture_output=True, text=True, timeout=180, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log("MANUAL", f"fonts — npm pack failed ({exc.__class__.__name__})")
            return

        tarballs = list(Path(tmp).glob("*.tgz"))
        if not tarballs:
            log("MANUAL", f"fonts — npm produced no tarball ({result.stdout.strip()[:60]})")
            return

        with tarfile.open(tarballs[0]) as archive:
            for name, expected in FONTS.items():
                member = f"package/fonts/webfonts/{name}"
                try:
                    handle = archive.extractfile(member)
                except KeyError:
                    handle = None
                if handle is None:
                    log("MANUAL", f"fonts — {name} missing from the package")
                    continue

                data = handle.read()
                if sha(data) != expected:
                    log("MANUAL", f"fonts — {name} checksum mismatch, not written")
                    continue

                (FONT_DIR / name).write_bytes(data)
                log("installed", f"static/fonts/{name}")


def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    report.append("\nApplying batch 6")
    apply_files()

    report.append("\nFonts")
    install_fonts()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  python -m ruff check . ; python -m pytest -q      # expect 101 passing\n"
        "  git add -A\n"
        '  git commit -m "Fix Jalali conversion, show next occurrence, archive past events"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Something was left alone — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
