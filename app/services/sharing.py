from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.config import Settings, get_settings
from app.db import get_events_collection

logger = logging.getLogger("tm_pro.sharing")

TOKEN_BYTES = 16

# What a public link and a Mini App deep link carry. "e_" keeps event payloads
# apart from the "r_" referral payloads Batch 12a introduced.
EVENT_PREFIX = "e_"


def generate_public_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def public_url(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"{settings.webapp_base_url.rstrip('/')}/c/{token}"


def card_url(token: str, settings: Settings | None = None) -> str:
    return f"{public_url(token, settings)}/card.png"


def miniapp_url(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={EVENT_PREFIX}{token}"
    )


def parse_event_payload(payload: str | None) -> str | None:
    """A start_param back into a public token, or None if it is not one."""
    value = (payload or "").strip()
    if not value.lower().startswith(EVENT_PREFIX):
        return None
    token = value[len(EVENT_PREFIX):]
    # token_urlsafe(16) is 22 characters from an unambiguous alphabet; anything
    # else never came from us and is not worth a database round trip.
    if not 16 <= len(token) <= 64 or not all(c.isalnum() or c in "-_" for c in token):
        return None
    return token


def _object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from None


async def _owned_event(user_id: str, event_id: str) -> dict:
    """Both _id and user_id in the filter — the IDOR-safe pattern used
    everywhere else in the codebase."""
    event = await get_events_collection().find_one(
        {"_id": _object_id(event_id), "user_id": user_id}
    )
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")
    return event


def describe(event: dict, settings: Settings | None = None) -> dict:
    """The share state of one event, as the Mini App wants to see it."""
    settings = settings or get_settings()

    enabled = bool(event.get("public_enabled"))
    token = event.get("public_token") if enabled else None

    return {
        "success": True,
        "enabled": enabled,
        "token": token,
        "public_url": public_url(token, settings) if token else None,
        "card_url": card_url(token, settings) if token else None,
        "miniapp_url": miniapp_url(token, settings) if token else None,
    }


async def get_share_state(user_id: str, event_id: str,
                          settings: Settings | None = None) -> dict:
    return describe(await _owned_event(user_id, event_id), settings)


async def set_share_state(user_id: str, event_id: str, enabled: bool,
                          settings: Settings | None = None) -> dict:
    """Turn public sharing on or off for one event.

    Switching off drops the token instead of parking it, so the old URL dies
    for good. Switching back on mints a fresh one — that is the whole point of
    a revocable link, and it is the behaviour someone expects when they take
    something off the internet.
    """
    event = await _owned_event(user_id, event_id)
    events = get_events_collection()
    now = datetime.now(timezone.utc)

    if not enabled:
        await events.update_one(
            {"_id": event["_id"], "user_id": user_id},
            {
                "$set": {"public_enabled": False, "updated_at": now},
                "$unset": {"public_token": "", "public_since": ""},
            },
        )
        logger.info("sharing disabled user_id=%s event_id=%s", user_id, event["_id"])
        return describe({}, settings)

    token = event.get("public_token") or generate_public_token()
    for _ in range(5):
        try:
            await events.update_one(
                {"_id": event["_id"], "user_id": user_id},
                {
                    "$set": {
                        "public_enabled": True,
                        "public_token": token,
                        "public_since": now,
                        "updated_at": now,
                    }
                },
            )
            break
        except DuplicateKeyError:
            # 22 random bytes colliding is close to impossible, but "close to"
            # is not "never", and the cost of being wrong is one retry.
            token = generate_public_token()
    else:
        raise HTTPException(status_code=500, detail="TOKEN_ALLOCATION_FAILED")

    logger.info("sharing enabled user_id=%s event_id=%s", user_id, event["_id"])
    return describe({"public_enabled": True, "public_token": token}, settings)


async def get_public_event(token: str) -> dict | None:
    """The event behind a public link, or None.

    public_enabled is part of the query rather than checked afterwards, so a
    disabled event cannot be reached even if its token somehow survived.
    """
    if not token:
        return None
    return await get_events_collection().find_one(
        {"public_token": token, "public_enabled": True}
    )
