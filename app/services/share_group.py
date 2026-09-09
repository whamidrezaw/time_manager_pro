from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import Settings, get_settings
from app.db import get_events_collection
from app.utils.dates import first_schedule, normalize_reminders, safe_zoneinfo

logger = logging.getLogger("tm_pro.share_group")

SHARE_PREFIX = "s_"
TOKEN_BYTES = 12

OWNER = "owner"
MEMBER = "member"

# What the group holds in common: what the event is and when it happens.
# Everything else — the reminder time, the note, the pin, the timezone — is
# the reason each person keeps their own copy rather than sharing a document.
SHARED_FIELDS = (
    "title", "date_iso", "date_jalali", "all_day", "time_hm",
    "repeat", "repeat_until", "category",
)


def generate_share_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def parse_share_payload(payload: str | None) -> str | None:
    """A start_param back into a share token, or None if it is not one."""
    value = (payload or "").strip()
    if not value.lower().startswith(SHARE_PREFIX):
        return None
    token = value[len(SHARE_PREFIX):]
    if not 12 <= len(token) <= 64 or not all(c.isalnum() or c in "-_" for c in token):
        return None
    return token


def invite_url(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={SHARE_PREFIX}{token}"
    )


async def _owned(user_id: str, event_id) -> dict:
    event = await get_events_collection().find_one({"_id": event_id, "user_id": user_id})
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")
    return event


async def member_count(share_id: str | None) -> int:
    if not share_id:
        return 0
    return await get_events_collection().count_documents(
        {"share_id": share_id, "share_role": MEMBER}
    )


async def start_group(user_id: str, event_id, settings: Settings | None = None) -> dict:
    """Give this event an invite link, creating the group on first use.

    A member cannot invite: only the creator owns the shared fields, so only
    the creator can hand out a way into them.
    """
    settings = settings or get_settings()
    event = await _owned(user_id, event_id)

    if event.get("share_role") == MEMBER:
        raise HTTPException(status_code=403, detail="NOT_THE_OWNER")

    token = event.get("share_token")
    share_id = event.get("share_id")

    if not token or not share_id:
        token = generate_share_token()
        share_id = secrets.token_urlsafe(TOKEN_BYTES)
        await get_events_collection().update_one(
            {"_id": event["_id"], "user_id": user_id},
            {"$set": {
                "share_id": share_id,
                "share_role": OWNER,
                "share_token": token,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        logger.info("share group opened user_id=%s event_id=%s", user_id, event["_id"])

    # An invite is only worth sending if it shows the event, and the picture
    # lives behind the public token. Turning that on here rather than asking
    # is deliberate: someone who just pressed "share this event with someone"
    # has already decided to show it to that someone.
    from app.services.sharing import card_url, describe, public_url, set_share_state

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    share = describe(fresh or {}, settings)
    if not share["enabled"]:
        share = await set_share_state(user_id, str(event["_id"]), True, settings)

    public_token = share.get("token")

    return {
        "success": True,
        "token": token,
        "invite_url": invite_url(token, settings),
        # What actually gets posted: Telegram previews this one as the card.
        "public_url": public_url(public_token, settings) if public_token else None,
        "card_url": card_url(public_token, settings) if public_token else None,
        "members": await member_count(share_id),
    }


async def find_by_token(token: str) -> dict | None:
    if not token:
        return None
    return await get_events_collection().find_one({"share_token": token})


async def join_group(user_id: str, token: str, timezone_name: str,
                     settings: Settings | None = None) -> dict:
    """Create this user's own copy of a shared event."""
    settings = settings or get_settings()
    events = get_events_collection()

    origin = await find_by_token(token)
    if not origin:
        raise HTTPException(status_code=404, detail="INVITE_NOT_FOUND")
    if str(origin.get("user_id")) == str(user_id):
        raise HTTPException(status_code=400, detail="ALREADY_YOURS")

    share_id = origin.get("share_id")
    existing = await events.find_one({"share_id": share_id, "user_id": user_id})
    if existing:
        # Opening the same link twice is a normal thing to do, and it must not
        # quietly produce a second copy of the same birthday.
        return {"success": True, "already_joined": True, "id": str(existing["_id"])}

    from app.services.referrals import effective_event_limit

    count = await events.count_documents({"user_id": user_id})
    if count >= await effective_event_limit(user_id, settings):
        raise HTTPException(status_code=400, detail="EVENT_LIMIT_REACHED")

    tz, tz_name = safe_zoneinfo(timezone_name)
    reminders = normalize_reminders(None, all_day=bool(origin.get("all_day", True)))
    now = datetime.now(timezone.utc)

    try:
        event_utc, notify_utc = first_schedule(
            date_str=origin.get("date_iso", ""),
            tz=tz,
            all_day=bool(origin.get("all_day", True)),
            time_hm=origin.get("time_hm"),
            reminders=reminders,
            repeat=str(origin.get("repeat", "none")),
            repeat_until=origin.get("repeat_until"),
            now=now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_DATE") from exc

    copy = {field: origin.get(field) for field in SHARED_FIELDS}
    copy.update({
        "user_id": str(user_id),
        "note": "",                     # the note is personal, never inherited
        "pinned": False,
        "lang": origin.get("lang", "en"),
        "tz_name": tz_name,
        "reminders": reminders,
        "reminder_hour": 9,
        "reminder_minute": 0,
        "lead_repeat": "none",
        "event_ts_utc": event_utc,
        "next_notify_at": notify_utc,
        "notify_status": "pending" if notify_utc is not None else "done",
        "notify_attempts": 0,
        "share_id": share_id,
        "share_role": MEMBER,
        "shared_from": str(origin.get("user_id")),
        "created_at": now,
        "updated_at": now,
    })

    result = await events.insert_one(copy)
    logger.info("joined share group user_id=%s share_id=%s", user_id, share_id)
    return {"success": True, "already_joined": False, "id": str(result.inserted_id)}


async def propagate(owner_id: str, event: dict) -> int:
    """Push the creator's changes onto every member copy.

    Each copy is rescheduled with its own timezone and its own reminders
    rather than copying the creator's times — that is the whole reason these
    are separate documents.
    """
    share_id = event.get("share_id")
    if not share_id or event.get("share_role") != OWNER:
        return 0

    events = get_events_collection()
    shared = {field: event.get(field) for field in SHARED_FIELDS}
    now = datetime.now(timezone.utc)
    touched = 0

    async for copy in events.find({"share_id": share_id, "share_role": MEMBER}):
        tz, _ = safe_zoneinfo(copy.get("tz_name"))
        reminders = normalize_reminders(
            copy.get("reminders"),
            all_day=bool(shared.get("all_day", True)),
            legacy_hour=copy.get("reminder_hour", 9),
            legacy_minute=copy.get("reminder_minute", 0),
        )
        try:
            event_utc, notify_utc = first_schedule(
                date_str=shared.get("date_iso", ""),
                tz=tz,
                all_day=bool(shared.get("all_day", True)),
                time_hm=shared.get("time_hm"),
                reminders=reminders,
                repeat=str(shared.get("repeat", "none")),
                repeat_until=shared.get("repeat_until"),
                now=now,
            )
        except ValueError:
            logger.warning("could not reschedule member copy %s", copy.get("_id"))
            continue

        await events.update_one(
            {"_id": copy["_id"]},
            {"$set": {
                **shared,
                "reminders": reminders,
                "event_ts_utc": event_utc,
                "next_notify_at": notify_utc,
                "notify_status": "pending" if notify_utc is not None else "done",
                "notify_attempts": 0,
                "updated_at": now,
            }},
        )
        touched += 1

    if touched:
        logger.info("propagated to %s member copies share_id=%s", touched, share_id)
    return touched


async def cascade_delete(event: dict) -> int:
    """The creator deleted the event, so the copies go too.

    This removes documents from other people's accounts, which is why the
    confirmation the creator sees says so in as many words before it happens.
    """
    share_id = event.get("share_id")
    if not share_id or event.get("share_role") != OWNER:
        return 0

    result = await get_events_collection().delete_many(
        {"share_id": share_id, "share_role": MEMBER}
    )
    logger.info("cascaded delete to %s copies share_id=%s", result.deleted_count, share_id)
    return result.deleted_count
