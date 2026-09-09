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
    SaveChecklistRequest,
    SaveNoteRequest,
)
from app.schemas.responses import EventOut
from app.utils.dates import (
    build_lead_reminders,
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
        lead_repeat=doc.get("lead_repeat", "none"),
        share_role=doc.get("share_role"),
        # Personal, exactly like the note: a shared birthday does not carry
        # one person's shopping list onto everyone else's copy.
        checklist=[
            item for item in (doc.get("checklist") or [])
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ],
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

    # The nudges that run up to the event. Appended to the same list the
    # scheduler already reads, so nothing downstream needs to know they exist.
    lead_repeat = str(getattr(payload, "lead_repeat", "none") or "none")
    reminders = reminders + build_lead_reminders(lead_repeat, reminders)

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
        "lead_repeat":          lead_repeat,
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

    # Imported here rather than at module scope to keep this module's
    # import graph flat, the same way check_rate_limit_mongo pulls in
    # get_database. The limit is derived per user now, not a constant.
    from app.services.referrals import effective_event_limit

    count = await events_coll.count_documents({"user_id": user_id})
    if count >= await effective_event_limit(user_id, settings):
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

    # Batch 12a: an invite only counts once the invited person has actually
    # made something. After the insert, never before it — a save that fails
    # must not be able to award anyone a bonus.
    try:
        from app.services.referrals import (
            activate_referral_if_first_event,
            notify_referrer_bonus,
        )

        earned = await activate_referral_if_first_event(user_id)
        if earned:
            await notify_referrer_bonus(*earned)
    except Exception:
        logger.exception("referral activation failed user_id=%s", user_id)


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

    # A member may keep their own note, reminder and pin, but the shared
    # fields belong to the creator. Silently dropping them here rather than
    # rejecting the whole request keeps the edit form usable for a member.
    if existing.get("share_role") == "member":
        from app.services.share_group import SHARED_FIELDS

        event_data = {k: v for k, v in event_data.items() if k not in SHARED_FIELDS}

    await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": event_data},
    )

    if existing.get("share_role") == "owner":
        try:
            from app.services.share_group import propagate

            await propagate(user_id, {**existing, **event_data})
        except Exception:
            logger.exception("share propagation failed event_id=%s", oid)


async def delete_event_for_user(user_id: str, event_id: str) -> None:
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    # Deleted and returned in one step, because the document is what says
    # whether other people's copies have to go with it.
    removed = await events_coll.find_one_and_delete({"_id": oid, "user_id": user_id})
    if removed is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    if removed.get("share_role") == "owner":
        try:
            from app.services.share_group import cascade_delete

            await cascade_delete(removed)
        except Exception:
            logger.exception("share cascade failed event_id=%s", oid)


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


async def save_checklist_for_user(
    user_id: str,
    payload: SaveChecklistRequest,
) -> list[dict]:
    """Replace the whole checklist in one write.

    Sending the list rather than a diff means ticking a box and deleting a
    line take the same path, and two taps in quick succession cannot
    interleave into a half-applied state.
    """
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    items = [
        {"text": item.text.strip(), "done": bool(item.done)}
        for item in payload.checklist
        if item.text.strip()
    ]

    result = await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": {"checklist": items, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    return items


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
