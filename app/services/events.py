"""
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
    build_event_datetimes,
    calc_next_notify,
    expire_for_repeat,
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
        reminder_hour=doc.get("reminder_hour", 9),
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

    tz, tz_name = safe_zoneinfo(payload.timezone)
    try:
        notify_utc, event_utc = build_event_datetimes(
            date_str=payload.date,
            tz=tz,
            reminder_hour=payload.reminder_hour,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_DATE") from exc

    # If it's a recurring event and the chosen hour has already passed today,
    # roll the FIRST notification forward to the next real occurrence instead
    # of firing it immediately (build_event_datetimes has no "already past"
    # guard on its own — calc_next_notify does, so reuse it here).
    if repeat != "none" and notify_utc <= datetime.now(timezone.utc):
        rolled = calc_next_notify(
            base_date=event_utc,
            repeat=repeat,
            tz=tz,
            reminder_hour=payload.reminder_hour,
        )
        if rolled is not None:
            notify_utc = rolled

    return {
        "title":                title,
        "date_iso":             payload.date,
        "next_notify_at":       notify_utc,
        "event_ts_utc":         event_utc,
        "expire_at":            expire_for_repeat(notify_utc, repeat),
        "repeat":               repeat,
        "tz_name":              tz_name,
        "reminder_hour":        payload.reminder_hour,
        "repeat_until":         repeat_until,
        "notify_status":        "pending",
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
