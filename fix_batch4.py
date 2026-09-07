#!/usr/bin/env python3
"""
fix_batch4.py — TimeManager Pro, batch 4: search and filtering on the server.

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch4.py
    python -m ruff check . ; python -m pytest -q

The bug

  list_events_for_user only ever queried {"user_id": ...} and returned 50
  documents; the browser then filtered those 50. A user with 200 events could
  search for something real, sitting on page 3, and be told there was nothing.

What changes

  The query now carries the search term and the filter. Text is matched with an
  escaped, case-insensitive regex over title and note, and the term is also
  tried against both date formats and the category name — the same fields the
  browser used to look at. Escaping is not cosmetic: an unescaped term is both
  a query-injection surface and a way to hand the database a pathological
  pattern.

  date_jalali is now stored on the document rather than computed on read, since
  a Mongo query cannot search a field that does not exist. Documents written
  before this change are filled in by a one-off backfill at startup, which is a
  no-op on every boot after the first. Jalali conversion is not expressible in
  an aggregation pipeline, so it walks the documents in Python, in batches.

  Persian and Arabic-Indic digits are folded to ASCII, and both spellings are
  searched. Without that, a date typed on a Persian keyboard would never match
  a date_jalali stored as "1405/01/31".

  Reads get their own per-minute budget, separate from writes. A search box
  issues a request per query, and on one shared budget typing would have locked
  the user out of saving. Both counters live in the same rate-limit document,
  so the unique index does not need rebuilding.

  In the Mini App the search box is debounced at 350 ms, and the "nothing
  found" panel now distinguishes an empty search result from an empty account —
  comparing events to filteredEvents can no longer tell those apart.

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


CONTENT_1 = r'''from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    app_name: str = Field(default="TimeManager Pro", alias="APP_NAME")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        alias="APP_ENV",
    )
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    webapp_base_url: str = Field(default="http://127.0.0.1:8000", alias="WEBAPP_BASE_URL")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    telegram_initdata_max_age: int = Field(default=900, alias="TELEGRAM_INITDATA_MAX_AGE")
    telegram_initdata_future_skew: int = Field(default=60, alias="TELEGRAM_INITDATA_FUTURE_SKEW")

    @field_validator("bot_token", mode="before")
    @classmethod
    def strip_bot_token(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    telegram_webhook_secret: str = Field(..., alias="TELEGRAM_WEBHOOK_SECRET")
    telegram_bot_username: str = Field(default="Timemanager2026_bot", alias="TELEGRAM_BOT_USERNAME")
    telegram_mini_app_short_name: str = Field(default="app", alias="TELEGRAM_MINI_APP_SHORT_NAME")

    mongo_uri: str = Field(..., alias="MONGO_URI")
    mongo_db_name: str = Field(default="time_manager_pro", alias="MONGO_DB_NAME")

    max_title_len: int = Field(default=200, alias="MAX_TITLE_LEN")
    max_note_len: int = Field(default=2000, alias="MAX_NOTE_LEN")
    max_events_per_user: int = Field(default=500, alias="MAX_EVENTS_PER_USER")
    rate_limit_count: int = Field(default=30, alias="RATE_LIMIT_COUNT")
    # Reads are cheap and the search box fires one per query, so they get their
    # own budget. Writes stay on the tighter one.
    rate_limit_read_count: int = Field(default=120, alias="RATE_LIMIT_READ_COUNT")

    reminder_batch_size: int = Field(default=200, alias="REMINDER_BATCH_SIZE")
    stale_processing_secs: int = Field(default=300, alias="STALE_PROCESSING_SECS")
    reminder_poll_interval_secs: int = Field(default=30, alias="REMINDER_POLL_INTERVAL_SECS")
    default_reminder_hour: int = Field(default=9, alias="DEFAULT_REMINDER_HOUR")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @computed_field
    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @computed_field
    @property
    def is_staging(self) -> bool:
        return self.app_env == "staging"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate_critical(self) -> None:
        if not self.bot_token.strip():
            raise ValueError("BOT_TOKEN must not be empty")

        if not self.mongo_uri.strip():
            raise ValueError("MONGO_URI must not be empty")

        if not self.telegram_webhook_secret.strip():
            raise ValueError("TELEGRAM_WEBHOOK_SECRET must not be empty")

        if self.max_title_len < 10:
            raise ValueError("MAX_TITLE_LEN is unrealistically low")

        if self.max_note_len < 100:
            raise ValueError("MAX_NOTE_LEN is unrealistically low")

        if self.rate_limit_count < 1:
            raise ValueError("RATE_LIMIT_COUNT must be >= 1")

        if self.rate_limit_read_count < 1:
            raise ValueError("RATE_LIMIT_READ_COUNT must be >= 1")

        if self.reminder_batch_size < 1:
            raise ValueError("REMINDER_BATCH_SIZE must be >= 1")

        if not (0 <= self.default_reminder_hour <= 23):
            raise ValueError("DEFAULT_REMINDER_HOUR must be between 0 and 23")

        if self.telegram_initdata_future_skew < 0:
            raise ValueError("TELEGRAM_INITDATA_FUTURE_SKEW must be >= 0")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_critical()
    return settings
'''

CONTENT_2 = r'''from __future__ import annotations

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


async def ping_database() -> bool:
    db = get_database()
    await db.command("ping")
    return True
'''

CONTENT_3 = r'''from __future__ import annotations

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
    except Exception:
        logger.exception("date_jalali backfill failed; search on Jalali dates may be incomplete")

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

CONTENT_4 = r'''from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from telegram import Bot

from app.config import get_settings
from app.schemas.common import PaginationMeta
from app.schemas.requests import (
    AddEventRequest,
    DeleteEventRequest,
    EditEventRequest,
    ListEventsRequest,
    PinEventRequest,
    SaveNoteRequest,
)
from app.schemas.responses import (
    EventMutationResponse,
    ListEventsResponse,
    NoteResponse,
    PinResponse,
)
from app.services.auth import READ_SCOPE, get_authenticated_user_id
from app.services.events import (
    add_event_for_user,
    delete_event_for_user,
    edit_event_for_user,
    list_events_for_user,
    save_note_for_user,
    set_pin_for_user,
)

router = APIRouter(prefix="/api", tags=["events"])
logger = logging.getLogger("tm_pro.events")


@router.post("/list", response_model=ListEventsResponse)
async def api_list(request: Request, payload: ListEventsRequest) -> ListEventsResponse:
    user_id = await get_authenticated_user_id(request, payload.initData, scope=READ_SCOPE)
    targets, has_more = await list_events_for_user(user_id, payload)

    return ListEventsResponse(
        success=True,
        targets=targets,
        has_more=has_more,
        meta=PaginationMeta(
            has_more=has_more,
            returned=len(targets),
            skip=payload.skip,
        ),
    )


@router.post("/add", response_model=EventMutationResponse)
async def api_add(request: Request, payload: AddEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await add_event_for_user(user_id, payload)

    settings = get_settings()
    try:
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=user_id,
                text=f'✅ Event "{payload.title}" was saved successfully.',
            )
    except Exception as exc:
        logger.warning("Confirmation message failed: user_id=%s error=%s", user_id, exc)

    return EventMutationResponse(success=True)


@router.post("/edit", response_model=EventMutationResponse)
async def api_edit(request: Request, payload: EditEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await edit_event_for_user(user_id, payload)
    return EventMutationResponse(success=True)


@router.post("/delete", response_model=EventMutationResponse)
async def api_delete(request: Request, payload: DeleteEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await delete_event_for_user(user_id, payload.event_id)
    return EventMutationResponse(success=True)


@router.post("/note", response_model=NoteResponse)
async def api_note(request: Request, payload: SaveNoteRequest) -> NoteResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    note = await save_note_for_user(user_id, payload)
    return NoteResponse(success=True, note=note)


@router.post("/pin", response_model=PinResponse)
async def api_pin(request: Request, payload: PinEventRequest) -> PinResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    pinned = await set_pin_for_user(user_id, payload)
    return PinResponse(success=True, pinned=pinned)
'''

CONTENT_5 = r'''from __future__ import annotations

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

CONTENT_6 = r'''"""
app/services/auth.py — Fixed v2.0
Fixes:
  - 'signature' field now excluded from data_check_string (was: only 'hash' excluded)
  - Cleaner import order
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request
from pymongo import ReturnDocument

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.auth")

# ── Fallback in-memory rate store (for tests / DB-unavailable) ───────────────
_rate_store: dict[str, list[float]] = {}


# Reads and writes are counted separately: a search box issues a request per
# query, and sharing one budget would let typing lock the user out of saving.
READ_SCOPE = "read"
WRITE_SCOPE = "write"


def scope_limit(scope: str, settings: Settings) -> int:
    return settings.rate_limit_read_count if scope == READ_SCOPE else settings.rate_limit_count


def _prune_rate_history(key: str, window_seconds: int = 60) -> list[float]:
    now = time.time()
    history = [t for t in _rate_store.get(key, []) if now - t < window_seconds]
    _rate_store[key] = history
    return history


def _check_rate_limit_memory(
    user_id: str,
    settings: Settings,
    scope: str = WRITE_SCOPE,
) -> None:
    """Fallback in-memory rate limit — only used when MongoDB is unavailable."""
    key = f"{scope}:{user_id}"
    history = _prune_rate_history(key)
    if len(history) >= scope_limit(scope, settings):
        logger.warning("Rate limit exceeded (memory fallback): user_id=%s scope=%s", user_id, scope)
        raise HTTPException(status_code=429, detail="RATE_LIMIT")
    history.append(time.time())
    _rate_store[key] = history


def check_rate_limit(
    user_id: str,
    settings: Settings | None = None,
    scope: str = WRITE_SCOPE,
) -> None:
    """Sync version — only used in tests."""
    settings = settings or get_settings()
    _check_rate_limit_memory(user_id, settings, scope)


async def check_rate_limit_mongo(
    user_id: str,
    settings: Settings,
    scope: str = WRITE_SCOPE,
) -> None:
    try:
        from app.db import get_database
        db = get_database()
        rate_coll = db["rate_limits"]

        now = datetime.now(timezone.utc)
        bucket = now.replace(second=0, microsecond=0)

        # Both counters live in the same document, keyed the same way as before.
        # A separate document per scope would need the unique index on
        # (user_id, bucket) rebuilt, and dropping a live unique index is not
        # worth it for two integers.
        field = "read_count" if scope == READ_SCOPE else "count"

        doc = await rate_coll.find_one_and_update(
            {"user_id": user_id, "bucket": bucket},
            {
                "$inc": {field: 1},
                "$setOnInsert": {"ts": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        if int(doc.get(field, 0)) > scope_limit(scope, settings):
            raise HTTPException(status_code=429, detail="RATE_LIMIT")

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limit mongo unavailable, using memory fallback: %s", exc)
        _check_rate_limit_memory(user_id, settings, scope)


# ── Core auth functions ───────────────────────────────────────────────────────

# Telegram documents two verification paths for initData. The bot-token HMAC
# path builds the data-check-string from every received field except `hash`.
# The third-party Ed25519 path excludes `signature` as well. Newer clients send
# a `signature` field on both paths, which leaves real ambiguity about which
# string the HMAC was computed over — and picking the wrong one locks out every
# user of that client.
#
# So both are accepted. Each candidate is still verified against the bot token,
# so allowing either is exactly as strong as allowing one: an attacker who can
# forge neither string gains nothing from there being two of them.
_ALWAYS_EXCLUDED = frozenset({"hash"})
_SIGNATURE_KEY = "signature"


def build_data_check_string(
    parsed: dict[str, str],
    exclude_signature: bool = False,
) -> str:
    excluded = set(_ALWAYS_EXCLUDED)
    if exclude_signature:
        excluded.add(_SIGNATURE_KEY)
    filtered = {k: v for k, v in parsed.items() if k not in excluded}
    return "\n".join(f"{key}={value}" for key, value in sorted(filtered.items()))


def compute_telegram_hash(
    init_data_map: dict[str, str],
    bot_token: str,
    exclude_signature: bool = False,
) -> str:
    data_check_string = build_data_check_string(init_data_map, exclude_signature)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def hash_matches(init_data_map: dict[str, str], bot_token: str, received_hash: str) -> bool:
    """True when the received hash matches either accepted data-check-string.

    Only tries the second variant when a `signature` field is actually present,
    so the common case still costs one HMAC.
    """
    variants = [False] if _SIGNATURE_KEY not in init_data_map else [False, True]
    return any(
        hmac.compare_digest(
            compute_telegram_hash(init_data_map, bot_token, exclude_signature),
            received_hash,
        )
        for exclude_signature in variants
    )


def parse_init_data(init_data: str) -> dict[str, str]:
    if not init_data:
        raise HTTPException(status_code=403, detail="NO_DATA")

    parsed: dict[str, str] = {}
    for chunk in init_data.split("&"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parsed[key] = unquote(value)

    if not parsed:
        raise HTTPException(status_code=403, detail="INVALID_INIT_DATA")

    return parsed


def parse_init_user(user_raw: str) -> dict[str, Any]:
    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=403, detail="INVALID_USER_JSON") from exc
    if not isinstance(user_data, dict):
        raise HTTPException(status_code=403, detail="INVALID_USER")
    return user_data


def validate_auth_date(
    auth_date_raw: str | None,
    max_age_seconds: int,
    max_future_skew_seconds: int = 60,
    now_ts: int | None = None,
) -> None:
    try:
        auth_date = int(auth_date_raw or "0")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE") from exc

    if auth_date <= 0:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE")

    now = now_ts if now_ts is not None else int(time.time())

    if auth_date < now - max_age_seconds:
        raise HTTPException(status_code=403, detail="EXPIRED")

    if auth_date > now + max_future_skew_seconds:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE")


async def validate_init_data(
    request: Request,
    init_data: str,
    settings: Settings | None = None,
    scope: str = WRITE_SCOPE,
) -> dict[str, Any]:
    settings = settings or get_settings()

    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="MISCONFIGURED")

    parsed = parse_init_data(init_data)

    received_hash = parsed.get("hash")
    if not received_hash:
        raise HTTPException(status_code=403, detail="NO_HASH")

    if not hash_matches(parsed, settings.bot_token, received_hash):
        computed_hash = compute_telegram_hash(parsed, settings.bot_token)
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "Bad Telegram initData HMAC: ip=%s received=%s… computed=%s… "
            "auth_date=%s deploy_marker=TIMEPICKER_CI_BUILD",
            client_ip,
            received_hash[:8],
            computed_hash[:8],
            parsed.get("auth_date"),
        )
        raise HTTPException(status_code=403, detail="BAD_HASH")

    validate_auth_date(
        auth_date_raw=parsed.get("auth_date"),
        max_age_seconds=settings.telegram_initdata_max_age,
        max_future_skew_seconds=settings.telegram_initdata_future_skew,
    )

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=403, detail="NO_USER")

    user_data = parse_init_user(user_raw)
    user_id = str(user_data.get("id", ""))

    if not user_id or not user_id.isdigit():
        raise HTTPException(status_code=403, detail="INVALID_ID")

    await check_rate_limit_mongo(user_id, settings, scope)

    return {
        "user_id": user_id,
        "user": user_data,
        "auth_date": parsed.get("auth_date"),
        "raw": parsed,
    }

async def get_authenticated_user_id(
    request: Request,
    init_data: str,
    settings: Settings | None = None,
    scope: str = WRITE_SCOPE,
) -> str:
    auth_result = await validate_init_data(request, init_data, settings, scope)
    return auth_result["user_id"]
'''

CONTENT_7 = r'''"""
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

CONTENT_8 = r'''/**
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
    return map[detail] || `Error: ${detail || "Unknown error"}`;
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
        shortText: `${pluralize(diff.totalDays, "day")} ago`,
        fullText: `This event was ${pluralize(diff.totalDays, "day")} ago`,
        totalDays: -diff.totalDays,
      };
    }

    if (diff.totalDays === 0) {
      return {
        tone: "today",
        shortText: "Today! 🎉",
        fullText: "This event is today!",
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

    const fullText  = `${parts.join(", ")} remaining`;
    const shortText = `${shortParts.join(" ")} left`;

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
          <span>${escapeHtml(STATUS_LABELS[event.notify_status] || "Pending")}</span>
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
    if (els.composerTitle)    els.composerTitle.textContent    = "New Event";
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
    if (els.composerTitle)    els.composerTitle.textContent    = "Edit Event";
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
    if (els.detailStatus)       els.detailStatus.textContent        = STATUS_LABELS[ev.notify_status] || "—";
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
      showToast("Please enter an event title.", "error");
      els.title?.focus();
      return;
    }
    if (!payload.date) {
      showToast("Please select a date.", "error");
      els.date?.focus();
      return;
    }
    if (!allDay && !eventTime) {
      showToast("Please set the event time, or mark it as an all-day event.", "error");
      els.eventTime?.focus();
      return;
    }

    setLoading(true);
    try {
      if (state.editingEventId) {
        // ✅ FIX: event_id (was: eventid)
        await apiPost("/api/edit", { event_id: state.editingEventId, ...payload });
        showToast("Event updated successfully.", "success");
      } else {
        await apiPost("/api/add", payload);
        showToast("Event saved! You'll receive a reminder in Telegram.", "success");
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
      showToast("Event deleted.", "success");
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
      showToast("Note saved.", "success");
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
      `🏷️ Category: ${CATEGORY_PLAIN[ev.category] || "General"}`,
      ev.note ? `📝 Note: ${ev.note}` : "",
    ].filter(Boolean).join("\n");

    try {
      if (navigator.share) {
        await navigator.share({ title: ev.title, text });
        showToast("Shared!", "success");
        return;
      }
      await copyToClipboard(text);
      showToast("Event details copied to clipboard.", "success");
    } catch (_) {
      showToast("Could not share. Please try copying manually.", "error");
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
    if (els.onboardingTitle) els.onboardingTitle.textContent = step.title;
    if (els.onboardingText)  els.onboardingText.textContent  = step.text;
    if (els.onboardingNextBtn) {
      els.onboardingNextBtn.textContent =
        onboardingStep === ONBOARDING_STEPS.length - 1 ? "Get Started" : "Next";
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
  initTelegram();
  bindEvents();
  loadEvents();
  showOnboardingIfNeeded();
})();
'''

CONTENT_9 = r'''from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.services.auth import (
    build_data_check_string,
    check_rate_limit,
    compute_telegram_hash,
    parse_init_data,
    parse_init_user,
    validate_auth_date,
    validate_init_data,
)


def make_request(ip: str = "127.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_parse_init_data_success() -> None:
    parsed = parse_init_data("query_id=abc&auth_date=123&hash=xyz")
    assert parsed["query_id"] == "abc"
    assert parsed["auth_date"] == "123"
    assert parsed["hash"] == "xyz"


def test_parse_init_data_rejects_empty() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_init_data("")
    assert exc.value.status_code == 403
    assert exc.value.detail == "NO_DATA"


def test_parse_init_user_success() -> None:
    raw = json.dumps({"id": 12345, "first_name": "Ali"})
    parsed = parse_init_user(raw)
    assert parsed["id"] == 12345
    assert parsed["first_name"] == "Ali"


def test_parse_init_user_rejects_invalid_json() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_init_user("{bad json}")
    assert exc.value.status_code == 403
    assert exc.value.detail == "INVALID_USER_JSON"


def test_build_data_check_string_always_excludes_hash() -> None:
    parsed = {
        "auth_date": "111",
        "user": '{"id":1}',
        "hash": "abc",
        "signature": "sig",
        "query_id": "q1",
    }
    result = build_data_check_string(parsed)

    assert "hash=abc" not in result
    assert "auth_date=111" in result
    assert "query_id=q1" in result
    # Default variant follows the bot-token path in Telegram's docs, which
    # excludes only `hash`.
    assert "signature=sig" in result


def test_build_data_check_string_can_also_exclude_signature() -> None:
    parsed = {"auth_date": "111", "hash": "abc", "signature": "sig"}
    result = build_data_check_string(parsed, exclude_signature=True)

    assert "signature=" not in result
    assert "auth_date=111" in result


def test_hash_matches_accepts_either_data_check_string() -> None:
    """Whichever variant Telegram's client actually signed, verification passes;
    both are checked against the bot token, so neither can be forged."""
    from app.services.auth import compute_telegram_hash, hash_matches

    token = "123456:TEST"
    parsed = {"auth_date": "111", "user": '{"id":1}', "signature": "sig"}

    with_signature = compute_telegram_hash(parsed, token, exclude_signature=False)
    without_signature = compute_telegram_hash(parsed, token, exclude_signature=True)

    assert with_signature != without_signature
    assert hash_matches(parsed, token, with_signature)
    assert hash_matches(parsed, token, without_signature)
    assert not hash_matches(parsed, token, "0" * 64)


def test_hash_matches_rejects_a_wrong_token() -> None:
    from app.services.auth import compute_telegram_hash, hash_matches

    parsed = {"auth_date": "111", "signature": "sig"}
    genuine = compute_telegram_hash(parsed, "123456:TEST")

    assert not hash_matches(parsed, "999999:OTHER", genuine)


def test_compute_telegram_hash_matches_manual_hmac() -> None:
    parsed = {
        "auth_date": "111",
        "query_id": "q1",
        "user": '{"id":1}',
    }
    token = "test_bot_token"

    data_check_string = "auth_date=111\nquery_id=q1\nuser={\"id\":1}"
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    actual = compute_telegram_hash(parsed, token)
    assert actual == expected


def test_validate_auth_date_accepts_recent_value() -> None:
    validate_auth_date(
        auth_date_raw="1000",
        max_age_seconds=900,
        max_future_skew_seconds=60,
        now_ts=1050,
    )


def test_validate_auth_date_rejects_old_value() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_auth_date(
            auth_date_raw="1000",
            max_age_seconds=10,
            max_future_skew_seconds=60,
            now_ts=2000,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "EXPIRED"


def test_validate_auth_date_rejects_far_future_value() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_auth_date(
            auth_date_raw="5000",
            max_age_seconds=900,
            max_future_skew_seconds=60,
            now_ts=1000,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "INVALID_AUTH_DATE"


def test_check_rate_limit_allows_under_limit() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()
    check_rate_limit("1", settings)
    # Reads and writes are counted separately, so the store is keyed by scope.
    assert "write:1" in auth_module._rate_store


def test_check_rate_limit_blocks_when_limit_reached() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()

    # Must be RECENT timestamps — the real rate limiter correctly prunes
    # anything older than the 60s window before counting, so seeding with
    # ancient timestamps (e.g. 1.0) makes this test pass even when the real
    # limiter is broken, and fail even when it's working correctly.
    now = time.time()
    auth_module._rate_store["write:99"] = [now - 1] * settings.rate_limit_count
    with pytest.raises(HTTPException) as exc:
        check_rate_limit("99", settings)

    assert exc.value.status_code == 429
    assert exc.value.detail == "RATE_LIMIT"


@pytest.mark.anyio
async def test_validate_init_data_success() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()

    user_json = json.dumps({"id": 123456, "first_name": "Test"})
    parsed = {
        "auth_date": "1000",
        "query_id": "AAEAAQ",
        "user": user_json,
    }
    parsed["hash"] = compute_telegram_hash(parsed, settings.bot_token)

    init_data = (
        f"auth_date={parsed['auth_date']}"
        f"&query_id={parsed['query_id']}"
        f"&user={user_json}"
        f"&hash={parsed['hash']}"
    )

    import app.services.auth as auth_service

    original_time = auth_service.time.time
    auth_service.time.time = lambda: 1050
    try:
        result = await validate_init_data(make_request(), init_data, settings)
    finally:
        auth_service.time.time = original_time

    assert result["user_id"] == "123456"
    assert result["user"]["first_name"] == "Test"


@pytest.mark.anyio
async def test_validate_init_data_rejects_bad_hash() -> None:
    settings = get_settings()
    user_json = json.dumps({"id": 123456})

    init_data = (
        f"auth_date=1000"
        f"&query_id=AAEAAQ"
        f"&user={user_json}"
        f"&hash=bad_hash"
    )

    import app.services.auth as auth_service

    original_time = auth_service.time.time
    auth_service.time.time = lambda: 1050
    try:
        with pytest.raises(HTTPException) as exc:
            await validate_init_data(make_request(), init_data, settings)
    finally:
        auth_service.time.time = original_time

    assert exc.value.status_code == 403
    assert exc.value.detail == "BAD_HASH"


def test_read_and_write_rate_limits_are_counted_separately() -> None:
    """Typing in the search box must not be able to lock the user out of saving,
    and vice versa."""
    from app.services import auth as auth_module
    from app.services.auth import READ_SCOPE, WRITE_SCOPE

    auth_module._rate_store.clear()
    settings = get_settings()
    now = time.time()

    auth_module._rate_store["write:42"] = [now - 1] * settings.rate_limit_count

    with pytest.raises(HTTPException):
        check_rate_limit("42", settings, scope=WRITE_SCOPE)

    check_rate_limit("42", settings, scope=READ_SCOPE)
    assert "read:42" in auth_module._rate_store


def test_read_rate_limit_uses_the_read_budget() -> None:
    from app.services import auth as auth_module
    from app.services.auth import READ_SCOPE

    auth_module._rate_store.clear()
    settings = get_settings()
    now = time.time()

    auth_module._rate_store["read:43"] = [now - 1] * settings.rate_limit_read_count

    with pytest.raises(HTTPException) as excinfo:
        check_rate_limit("43", settings, scope=READ_SCOPE)

    assert excinfo.value.status_code == 429
'''

CONTENT_10 = r'''from __future__ import annotations

from datetime import timedelta

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
'''

CONTENT_11 = r'''from __future__ import annotations

import re

# Persian and Arabic-Indic digits map onto ASCII so that a date typed on a
# Persian keyboard matches a date_jalali stored as "1405/01/31".
_DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_digits(value: str) -> str:
    return (value or "").translate(_DIGIT_MAP)


def search_variants(term: str) -> list[str]:
    """The forms of a search term worth matching against.

    A user may type "۳" while the stored title reads "3", or the other way
    round, so both spellings are tried whenever they differ.
    """
    term = (term or "").strip()
    if not term:
        return []

    normalized = normalize_digits(term)
    return [term] if normalized == term else [term, normalized]


def regex_clause(field: str, term: str, case_insensitive: bool = True) -> dict:
    """A MongoDB regex clause with the term escaped.

    Escaping is not cosmetic: an unescaped term is both a query-injection
    surface and a way to hand the database a pathological pattern that pins its
    CPU for as long as the attacker keeps sending it.
    """
    clause: dict = {"$regex": re.escape(term)}
    if case_insensitive:
        clause["$options"] = "i"
    return {field: clause}
'''

CONTENT_12 = r'''from __future__ import annotations

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

def test_plain_list_query_is_scoped_to_the_user() -> None:
    query = build_list_query("42", _request())
    assert query == {"user_id": "42"}


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


def test_list_request_rejects_an_unknown_filter() -> None:
    with pytest.raises(ValueError):
        _request(filter="'; drop everything")
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "app/config.py",
        "92e5bd498726a4840081525831085688dd457e8b2d1acacd21ff0743faefc7b4",
        "538e49d088cf80731c540d1bdfd01aed27f42a13a21f8ca90c544b3aa71dd2b1",
        CONTENT_1,
    ),
    (
        "app/db.py",
        "bcd898673c53b49d93c24ced72015a743f415575aa0299b3c6bfcfdec2368f5c",
        "5548e30dac42dc237ed269c9688e9eb5edfcd38a04f0ecd4b103c9cd4ba79432",
        CONTENT_2,
    ),
    (
        "app/main.py",
        "601e9ff9f4cd3d40d267ee0c4c917cb173260b37f5d4986206236e11b231fc96",
        "e7049f739e5bbb4f62f313eefab4f75e7b8404f3eaa66cfeef29f71f1cd7d469",
        CONTENT_3,
    ),
    (
        "app/routes/events.py",
        "b8f09ab74da565feade59d265efc215c201bad861f13a1909c37ec86b5d2b8be",
        "d9e20e5c33359b00ad10707fadc25706ceb90c4bcf2568a169868e74c9691097",
        CONTENT_4,
    ),
    (
        "app/schemas/requests.py",
        "02897f33c63dd0d091aebfd8454e35ba93affe395382fe49b939ae7862bbb3e9",
        "ff08e8a0ccfc52bddbd4835b5cf5dc97a278d97e5ff2c2c9f4fe8deffa2ceaeb",
        CONTENT_5,
    ),
    (
        "app/services/auth.py",
        "b6c0b58a8d983369247b2511a24247e509052cf8d39a8dc8044aec046c491a61",
        "1e59652bb052eebf28a6f3cb6a8ed95a28802ee483fc4b2b85cfc5337e668c49",
        CONTENT_6,
    ),
    (
        "app/services/events.py",
        "9696fdbb2bb24701b65b009e3e7c3e023ded1b140045640f3db55b22f336a1cb",
        "2dd9f6f3ecec01e917f8acdc81abb0291e7de09f0367bd94ac5915790b732e18",
        CONTENT_7,
    ),
    (
        "static/app.js",
        "0dfad908d6c40651fdf106b6427a8e0f6f3b8185f39d5f16f95518a56391c1a4",
        "c9ba0ef419a6b64888f8cc6bd58e312896afbb098f6c2b24ae250421afe465a9",
        CONTENT_8,
    ),
    (
        "tests/test_auth.py",
        "bb3c008d34e190c7ac4ba13836e369a07a12a4469403ae59b03701ede7b45c4d",
        "ce1b4b1e2ad74063fd107b4eb47cca41f4d7f4c8271bee5dec1167a627e72e8b",
        CONTENT_9,
    ),
    (
        "tests/test_events_api.py",
        "5cef103be7bb307f687bc938704e90666f2a4a8639a6656e2864988c78cff6b6",
        "b8a4e3ff1d56c07f763d6e4e6731643d0dd72966e9cff2aa6eb19dd3a33a914e",
        CONTENT_10,
    ),
    (
        "app/utils/text.py",
        "NEW",
        "2a6cef4522e1a2cc6ee42a3b0215409ce028c53e4d2bffef08acc63733dfb917",
        CONTENT_11,
    ),
    (
        "tests/test_search.py",
        "NEW",
        "184d3b1a0f0d4d8ff1d4ae35cf5f14e6a9adeae63745a14ab39f05231a756d21",
        CONTENT_12,
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

    report.append("\nApplying batch 4 — server-side search")
    apply_files()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  python -m ruff check . ; python -m pytest -q      # expect 79 passing\n"
        "  git add -A\n"
        '  git commit -m "Move search and filtering to the server"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Some files were left untouched — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
