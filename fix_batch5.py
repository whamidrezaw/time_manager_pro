#!/usr/bin/env python3
"""
fix_batch5.py — TimeManager Pro, batch 5: Persian, with right-to-left layout.

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch5.py
    python -m ruff check . ; python -m pytest -q

Which language, and why only two

  The language comes from the language_code Telegram already sends in initData
  and on every bot update — no setting, no extra tap. A user whose Telegram is
  Persian gets Persian; everyone else gets English. Only those two, on purpose:
  Persian serves the audience the Jalali calendar is here for, and a German or
  Spanish speaker is better served by English than by a language they cannot
  read.

How the strings are keyed

  The translation table is keyed by the English source string, not by an
  invented id. That means the template needed no data-i18n attributes at all:
  translating it is one DOM pass at boot instead of 99 markup edits, and any
  string with no entry stays English rather than turning into a blank.

Right-to-left

  Cheaper than expected. The whole stylesheet had only four direction-specific
  rules, because the layout is flexbox and grid throughout, so dir="rtl" on
  <html> mirrors nearly everything by itself. Two of the four became logical
  properties; the select chevron is a background image with no logical
  equivalent and got an explicit RTL rule.

The bot

  /start, /help and the snooze replies read the sender's language straight off
  the update, so switching language in Telegram takes effect immediately. The
  reminder worker has no initData when it fires, so the language travels on the
  event document — the same way tz_name already does. Events written before
  this change have no lang and fall back to English.

Safe to run twice. Every file that already exists is checked against a hash of
the version this script was built against; anything unexpected is reported and
left untouched.
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

from fastapi import APIRouter, Header, HTTPException, Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import Settings, get_settings
from app.services.reminders import handle_snooze_callback
from app.utils.i18n import resolve_language, t

logger = logging.getLogger("tm_pro.telegram")

router = APIRouter(prefix="/telegram", tags=["telegram"])



@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    settings = get_settings()

    # Confirms the request genuinely came from Telegram, not just anyone who
    # found this URL. Telegram echoes this header back on every webhook call
    # when a secret_token was set via set_webhook (see app/main.py lifespan).
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="BAD_SECRET_TOKEN")

    payload = await request.json()

    # Plain construction only — no network I/O, just needed so de_json can
    # attach a bot reference to the parsed objects.
    update = Update.de_json(payload, Bot(token=settings.bot_token))

    if update is None:
        return {"ok": True}

    # A live, initialized Bot is only needed for the actual API call, so it
    # stays scoped to the branches that really talk to Telegram.
    if update.callback_query:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_callback_query(update, bot)
    elif update.message:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_message(update, bot, settings)

    return {"ok": True}


def build_open_app_keyboard(settings: Settings, language: str = "en") -> InlineKeyboardMarkup:
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("open_app_button", language), url=deep_link)]]
    )


def parse_command(text: str) -> str:
    """Normalise a message into a bare command.

    '/start@MyBot some-payload' -> '/start'. Anything that is not a command
    (plain text, empty, a caption-less photo) -> ''.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return ""
    return parts[0].split("@", 1)[0].lower()


async def _handle_message(update: Update, bot: Bot, settings: Settings) -> None:
    message = update.message
    command = parse_command(message.text or "")

    # No storage needed here: Telegram hands us the sender's language on every
    # update, so a user who switches language sees the change immediately.
    language = resolve_language(getattr(message.from_user, "language_code", None))

    if command == "/start":
        key = "start"
    elif command == "/help":
        key = "help"
    else:
        key = "fallback"

    try:
        await bot.send_message(
            chat_id=message.chat_id,
            text=t(key, language),
            parse_mode="HTML",
            reply_markup=build_open_app_keyboard(settings, language),
        )
    except Exception:
        logger.exception("Failed to reply to chat_id=%s", message.chat_id)


async def _handle_callback_query(update: Update, bot: Bot) -> None:
    query = update.callback_query
    data = query.data or ""
    language = resolve_language(getattr(query.from_user, "language_code", None))

    try:
        action, event_id = data.split(":", 1)
    except ValueError:
        await _safe_answer(bot, query.id, t("unknown_action", language))
        return

    if action == "snooze1h":
        # Authorization lives in handle_snooze_callback: it matches on both
        # _id and user_id (query.from_user.id), the same IDOR-safe pattern
        # used everywhere else in app/services/events.py.
        ok = await handle_snooze_callback(event_id, query.from_user.id, seconds=3600)
        text = t("snoozed" if ok else "snooze_failed", language)
    else:
        text = t("unknown_action", language)

    await _safe_answer(bot, query.id, text)


async def _safe_answer(bot: Bot, callback_query_id: str, text: str) -> None:
    try:
        await bot.answer_callback_query(callback_query_id, text=text)
    except Exception:
        logger.exception("Failed to answer callback query id=%s", callback_query_id)
'''

CONTENT_2 = r'''from __future__ import annotations

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

CONTENT_3 = r'''"""
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


def serialize_event(doc: dict) -> EventOut:
    date_iso = doc.get("date_iso", "")
    all_day = bool(doc.get("all_day", True))
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


def build_list_query(user_id: str, payload: ListEventsRequest) -> dict:
    """Mongo filter for one page of a user's events.

    Every branch keeps user_id in the filter, so the regex search can only ever
    scan the documents belonging to the caller.
    """
    query: dict = {"user_id": user_id}

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

CONTENT_4 = r'''from __future__ import annotations

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
    safe_zoneinfo,
    to_jalali,
)
from app.utils.i18n import DEFAULT_LANGUAGE, category_label, repeat_label, t
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


def event_language(evt: dict) -> str:
    return evt.get("lang") or DEFAULT_LANGUAGE


def build_reminder_text(evt: dict) -> str:
    lang = event_language(evt)
    repeat = evt.get("repeat", "none")
    date_iso = evt.get("date_iso", "")
    jalali_date = evt.get("date_jalali") or to_jalali(date_iso)
    category = evt.get("category", "general")
    pin_mark = "📌 " if evt.get("pinned") else ""
    title = html.escape(evt.get("title", ""))

    time_line = ""
    if not evt.get("all_day", True) and evt.get("time_hm"):
        time_line = f"🕒 {html.escape(str(evt['time_hm']))}\n"

    return (
        f"{t('reminder_title', lang)}\n"
        f"{pin_mark}{title}\n"
        f"📅 {date_iso}  •  {jalali_date}\n"
        f"{time_line}"
        f"🏷️ {html.escape(category_label(category, lang))}\n"
        f"🔄 {repeat_label(repeat, lang)}"
    )


def build_reminder_keyboard(evt: dict, settings: Settings) -> InlineKeyboardMarkup:
    event_id = object_id_str(evt["_id"])
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={event_id}"
    )

    lang = event_language(evt)

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("snooze_button", lang),
                    callback_data=f"snooze1h:{event_id}",
                ),
                InlineKeyboardButton(t("open_button", lang), url=deep_link),
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

CONTENT_5 = r'''/**
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
      const cd = getCountdownData(event.date_iso);
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
          <span>📅 ${escapeHtml(event.date_iso || "—")}${
            event.all_day === false && event.time_hm
              ? ` · ${escapeHtml(event.time_hm)}`
              : ""
          }</span>
          <span class="event-dates-sep">•</span>
          <span>🗓️ ${escapeHtml(event.date_jalali || "—")}</span>
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
    const cd = getCountdownData(ev.date_iso);

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

  function jalaliToGregorian(jy, jm, jd) {
    let gy = jy > 979 ? (gy = 1600, jy -= 979, 1600) : (jy -= 0, 621);
    if (jy > 979) { gy = 1600; jy -= 979; } else { gy = 621; }
    let days = 365*jy + Math.floor(jy/33)*8 + Math.floor(((jy%33)+3)/4) + 78 + jd +
      (jm < 7 ? (jm-1)*31 : (jm-7)*30 + 186);
    gy += 400*Math.floor(days/146097); days %= 146097;
    if (days > 36524) { gy += 100*Math.floor(--days/36524); days %= 36524; if (days >= 365) days++; }
    gy += 4*Math.floor(days/1461); days %= 1461;
    if (days > 365) { gy += Math.floor((days-1)/365); days = (days-1)%365; }
    let gd = days + 1;
    const sal = [0,31,((gy%4===0&&gy%100!==0)||gy%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
    let gm = 0;
    for (gm = 1; gm <= 12; gm++) { if (gd <= sal[gm]) break; gd -= sal[gm]; }
    return { gy, gm, gd };
  }

  function gregorianToJalali(gy, gm, gd) {
    const g_d_m = [0,31,59,90,120,151,181,212,243,273,304,334];
    let jy = gy > 1600 ? (gy -= 1600, 979) : (gy -= 621, 0);
    const gy2 = gm > 2 ? gy+1 : gy;
    let days = 365*gy + Math.floor((gy2+3)/4) - Math.floor((gy2+99)/100) +
      Math.floor((gy2+399)/400) - 80 + gd + g_d_m[gm-1];
    jy += 33*Math.floor(days/12053); days %= 12053;
    jy += 4*Math.floor(days/1461); days %= 1461;
    if (days > 365) { jy += Math.floor((days-1)/365); days = (days-1)%365; }
    const jm = days < 186 ? 1+Math.floor(days/31) : 7+Math.floor((days-186)/30);
    const jd = 1 + (days < 186 ? days%31 : (days-186)%30);
    return { jy, jm, jd };
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
    const g = jalaliToGregorian(jy, jm, jd);
    if (els.date) els.date.value = `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
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

CONTENT_6 = r'''/* ═══════════════════════════════════════════════════════════
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
  --font: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
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

CONTENT_7 = r'''from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "fa")


def resolve_language(language_code: str | None) -> str:
    """Map a Telegram language_code onto a language this app actually speaks.

    Only Persian and English are offered, and that is deliberate rather than a
    gap: Persian serves the audience the Jalali calendar is here for, and
    English serves everyone else. A German or Spanish speaker is better off in
    English than in a language they do not read.
    """
    code = (language_code or "").strip().lower()
    return "fa" if code.startswith("fa") else DEFAULT_LANGUAGE


_STRINGS: dict[str, dict[str, str]] = {
    "start": {
        "en": (
            "👋 <b>Welcome to TimeManager Pro!</b>\n\n"
            "Save birthdays, meetings and anything else you need to remember — and get "
            "the reminder right here in Telegram, on both the Gregorian and the Jalali "
            "calendar.\n\n"
            "Tap the button below to open the app and add your first event."
        ),
        "fa": (
            "👋 <b>به تایم‌منیجر پرو خوش آمدید!</b>\n\n"
            "تولدها، جلسه‌ها و هر چیز دیگری را که می‌خواهید به یاد داشته باشید ذخیره کنید "
            "و یادآوری‌اش را همین‌جا در تلگرام بگیرید، هم به تاریخ میلادی و هم شمسی.\n\n"
            "برای باز کردن برنامه و ثبت اولین رویداد، دکمه زیر را بزنید."
        ),
    },
    "help": {
        "en": (
            "<b>TimeManager Pro — Help</b>\n\n"
            "/start — open the app and see the welcome message\n"
            "/help — show this message\n\n"
            "Everything else happens inside the Mini App: add, edit, pin and delete "
            "events, write notes, and pick the exact time you want to be reminded.\n\n"
            "When a reminder arrives, tap <b>⏰ Snooze 1h</b> to push it back an hour, "
            "or <b>📖 Open</b> to jump straight to that event."
        ),
        "fa": (
            "<b>تایم‌منیجر پرو — راهنما</b>\n\n"
            "/start — باز کردن برنامه و دیدن پیام خوش‌آمد\n"
            "/help — نمایش همین پیام\n\n"
            "بقیه کارها داخل خود برنامه انجام می‌شود: افزودن، ویرایش، سنجاق و حذف رویداد، "
            "نوشتن یادداشت، و انتخاب دقیق زمانی که می‌خواهید یادآوری شوید.\n\n"
            "وقتی یادآوری رسید، با <b>⏰ یک ساعت بعد</b> آن را یک ساعت به تعویق بیندازید "
            "یا با <b>📖 باز کردن</b> مستقیم به همان رویداد بروید."
        ),
    },
    "fallback": {
        "en": (
            "I don't understand plain text messages yet 🙂\n\n"
            "Send /help to see what I can do, or open the app with the button below."
        ),
        "fa": (
            "هنوز پیام‌های متنی معمولی را متوجه نمی‌شوم 🙂\n\n"
            "برای دیدن کارهایی که بلدم /help را بفرستید، یا با دکمه زیر برنامه را باز کنید."
        ),
    },
    "open_app_button": {"en": "📅 Open TimeManager Pro", "fa": "📅 باز کردن تایم‌منیجر پرو"},
    "snooze_button":   {"en": "⏰ Snooze 1h",            "fa": "⏰ یک ساعت بعد"},
    "open_button":     {"en": "📖 Open",                "fa": "📖 باز کردن"},
    "snoozed":  {
        "en": "⏰ Snoozed — you'll be reminded again in 1 hour.",
        "fa": "⏰ به تعویق افتاد — یک ساعت دیگر دوباره یادآوری می‌شود.",
    },
    "snooze_failed": {
        "en": "Couldn't snooze that event.",
        "fa": "به تعویق انداختن این رویداد ممکن نشد.",
    },
    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},
    "reminder_title": {"en": "🔔 <b>Reminder</b>", "fa": "🔔 <b>یادآوری</b>"},
    "label_category": {"en": "🏷️", "fa": "🏷️"},
    "repeat_none":    {"en": "One-time",  "fa": "یک‌بار"},
    "repeat_daily":   {"en": "🔁 Daily",   "fa": "🔁 هر روز"},
    "repeat_weekly":  {"en": "🔁 Weekly",  "fa": "🔁 هر هفته"},
    "repeat_monthly": {"en": "🔁 Monthly", "fa": "🔁 هر ماه"},
    "repeat_yearly":  {"en": "🎂 Yearly",  "fa": "🎂 هر سال"},
    "category_general":  {"en": "General",  "fa": "عمومی"},
    "category_birthday": {"en": "Birthday", "fa": "تولد"},
    "category_work":     {"en": "Work",     "fa": "کاری"},
    "category_family":   {"en": "Family",   "fa": "خانواده"},
    "category_health":   {"en": "Health",   "fa": "سلامت"},
    "category_travel":   {"en": "Travel",   "fa": "سفر"},
    "category_finance":  {"en": "Finance",  "fa": "مالی"},
    "category_study":    {"en": "Study",    "fa": "درسی"},
    "category_other":    {"en": "Other",    "fa": "سایر"},
}


def t(key: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Look up a string, falling back to English and then to the key itself."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(language) or entry.get(DEFAULT_LANGUAGE, key)


def repeat_label(repeat: str, language: str = DEFAULT_LANGUAGE) -> str:
    return t(f"repeat_{repeat}", language) if f"repeat_{repeat}" in _STRINGS else t("repeat_none", language)


def category_label(category: str, language: str = DEFAULT_LANGUAGE) -> str:
    key = f"category_{category}"
    return t(key, language) if key in _STRINGS else t("category_general", language)
'''

CONTENT_8 = r'''from __future__ import annotations

import pytest

from app.services.reminders import build_reminder_text, event_language
from app.utils.i18n import (
    DEFAULT_LANGUAGE,
    category_label,
    repeat_label,
    resolve_language,
    t,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("fa", "fa"),
        ("fa-IR", "fa"),
        ("FA", "fa"),
        ("de", "en"),
        ("es", "en"),
        ("en-GB", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_resolve_language(code, expected) -> None:
    """Persian for the audience the Jalali calendar is here for, English for
    everyone else — a German speaker is better served by English than Persian."""
    assert resolve_language(code) == expected


def test_unknown_key_returns_the_key() -> None:
    assert t("no_such_key", "fa") == "no_such_key"


def test_missing_translation_falls_back_to_english() -> None:
    assert t("start", "de") == t("start", DEFAULT_LANGUAGE)


def test_labels_have_persian_forms() -> None:
    assert repeat_label("yearly", "fa") != repeat_label("yearly", "en")
    assert category_label("work", "fa") != category_label("work", "en")


def test_unknown_repeat_and_category_fall_back() -> None:
    assert repeat_label("nonsense", "en") == repeat_label("none", "en")
    assert category_label("nonsense", "en") == category_label("general", "en")


def _event(**overrides) -> dict:
    base = {
        "title": "Standup",
        "date_iso": "2026-04-20",
        "date_jalali": "1405/01/31",
        "repeat": "yearly",
        "category": "work",
        "pinned": False,
        "all_day": True,
    }
    base.update(overrides)
    return base


def test_event_language_defaults_to_english() -> None:
    """Documents written before the field existed carry no lang."""
    assert event_language(_event()) == "en"
    assert event_language(_event(lang=None)) == "en"


def test_reminder_text_follows_the_stored_language() -> None:
    persian = build_reminder_text(_event(lang="fa"))
    english = build_reminder_text(_event(lang="en"))

    assert repeat_label("yearly", "fa") in persian
    assert repeat_label("yearly", "en") in english
    assert persian != english


def test_reminder_text_escapes_the_title_in_both_languages() -> None:
    for lang in ("en", "fa"):
        text = build_reminder_text(_event(lang=lang, title="<script>x</script>"))
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "app/routes/telegram.py",
        "80e88d3c7d99401ef703cb269ae71fc62861e5f9763256410ceead78de82d96d",
        "d146b10779212257a6c37dc457ab2dc4d348307c246716bce0edb6b3145acb60",
        CONTENT_1,
    ),
    (
        "app/schemas/requests.py",
        "ff08e8a0ccfc52bddbd4835b5cf5dc97a278d97e5ff2c2c9f4fe8deffa2ceaeb",
        "fc97ef07cde00150921e3a4f8e087b4a09aaf10ebca06de73bec5d27ee02854c",
        CONTENT_2,
    ),
    (
        "app/services/events.py",
        "2dd9f6f3ecec01e917f8acdc81abb0291e7de09f0367bd94ac5915790b732e18",
        "6e7bac7c5d07e21c8a9cbf82dd840bc69f9fb74565e75031b084b3c77be22cf6",
        CONTENT_3,
    ),
    (
        "app/services/reminders.py",
        "938dec9c1f72974501a3be47edcd45d94692217aa7f74d9c156ce1babf16e862",
        "e788ba958212707e7fc49a3773b7e6bd2715aeeca4c43fe8f2a9c2708575f432",
        CONTENT_4,
    ),
    (
        "static/app.js",
        "c9ba0ef419a6b64888f8cc6bd58e312896afbb098f6c2b24ae250421afe465a9",
        "e7f9bb77a8fdac549406485d016c5211b7a862d65c1906bbfca9dec756f823ce",
        CONTENT_5,
    ),
    (
        "static/style.css",
        "9b8fe5cdc4c00672a13c42df854ae261f66153cb8d0ee655d5a93a183b5b7161",
        "04ecd15e69d1fb96a41b338bc859107a06d795e144aa1efc6c78884af4e2f35a",
        CONTENT_6,
    ),
    (
        "app/utils/i18n.py",
        "NEW",
        "6b31588ff087b6dca79f247144967ce25e7d323f5394833d83bf6efd7089a623",
        CONTENT_7,
    ),
    (
        "tests/test_i18n.py",
        "NEW",
        "3fe052932eb2e99926ff60579787e56a1f59446007ada6f52047c9e1c49a605a",
        CONTENT_8,
    ),
]


def apply_files() -> None:
    for rel, expected_sha, new_sha, content in FILES:
        path = ROOT / rel

        if not path.exists():
            if expected_sha != "NEW":
                log("MISSING", f"{rel} — not found, skipped")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
            log("created", rel)
            continue

        digest = sha(path.read_bytes())

        if digest == new_sha:
            log("skipped", f"{rel} (already applied)")
            continue

        if expected_sha == "NEW" or digest != expected_sha:
            log("MANUAL", f"{rel} — not the version this script expects, left untouched")
            continue

        path.write_bytes(content.encode("utf-8"))
        log("updated", rel)


def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    report.append("\nApplying batch 5 — Persian and RTL")
    apply_files()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  python -m ruff check . ; python -m pytest -q      # expect 94 passing\n"
        "  git add -A\n"
        '  git commit -m "Add Persian localisation and RTL layout"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Some files were left untouched — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
