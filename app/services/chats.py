from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from app.config import Settings, get_settings
from app.db import get_chats_collection

logger = logging.getLogger("tm_pro.chats")

CHAT_PREFIX = "g_"
TOKEN_BYTES = 10

GROUP_TYPES = {"group", "supergroup"}
CHANNEL_TYPES = {"channel"}
# A private chat is not a destination — it is where reminders already go.
ALLOWED_TYPES = GROUP_TYPES | CHANNEL_TYPES

# Being present is enough for a group; a channel needs the right to post.
ACTIVE_STATUSES = {"member", "administrator", "creator"}
MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}


def generate_link_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def parse_chat_payload(payload: str | None) -> str | None:
    """A start_param back into a chat link token, or None."""
    value = (payload or "").strip()
    if not value.lower().startswith(CHAT_PREFIX):
        return None
    token = value[len(CHAT_PREFIX):]
    if not 10 <= len(token) <= 64 or not all(c.isalnum() or c in "-_" for c in token):
        return None
    return token


def chat_link(token: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={CHAT_PREFIX}{token}"
    )


def scope_for(chat_type: str | None) -> str:
    if str(chat_type) in CHANNEL_TYPES:
        return "channel"
    if str(chat_type) in GROUP_TYPES:
        return "group"
    return "private"


async def register_chat(chat_id: int, chat_type: str, title: str,
                        added_by: str | None) -> dict | None:
    """The bot was added somewhere. Remember it as a possible destination.

    The person who added it is linked straight away — they asked for this by
    doing it. Everyone else has to come through the link, which is where
    membership actually gets checked.
    """
    if chat_type not in ALLOWED_TYPES:
        return None

    chats = get_chats_collection()
    now = datetime.now(timezone.utc)
    existing = await chats.find_one({"_id": chat_id})

    members = list(existing.get("members", [])) if existing else []
    if added_by and str(added_by) not in members:
        members.append(str(added_by))

    document = {
        "type": chat_type,
        "title": title or "",
        "active": True,
        "members": members,
        "updated_at": now,
        "link_token": (existing or {}).get("link_token") or generate_link_token(),
    }
    await chats.update_one(
        {"_id": chat_id},
        {"$set": document, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    logger.info("chat registered id=%s type=%s", chat_id, chat_type)
    return {"_id": chat_id, **document}


async def deactivate_chat(chat_id: int) -> None:
    """The bot lost access. Keep the row so the reason can be shown, but stop
    offering it as somewhere to send things."""
    await get_chats_collection().update_one(
        {"_id": chat_id},
        {"$set": {"active": False, "updated_at": datetime.now(timezone.utc)}},
    )
    logger.info("chat deactivated id=%s", chat_id)


async def find_by_token(token: str) -> dict | None:
    if not token:
        return None
    return await get_chats_collection().find_one({"link_token": token})


async def link_member(user_id: str, token: str) -> dict:
    """Add a chat to one user's destinations, after checking they are in it.

    The check is the whole point: without it, anyone holding a forwarded link
    could post reminders into a group they have never been part of.
    """
    from app.services.telegram_api import get_chat_member_status

    chat = await find_by_token(token)
    if not chat or not chat.get("active"):
        return {"success": False, "reason": "NOT_FOUND"}

    status = await get_chat_member_status(chat["_id"], user_id)
    if status not in MEMBER_STATUSES:
        return {"success": False, "reason": "NOT_A_MEMBER"}

    await get_chats_collection().update_one(
        {"_id": chat["_id"]},
        {"$addToSet": {"members": str(user_id)},
         "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    return {"success": True, "chat_id": chat["_id"], "title": chat.get("title", "")}


async def destinations_for(user_id: str) -> list[dict]:
    """The chats this user may send to — never a list of every chat the bot
    happens to know about."""
    cursor = get_chats_collection().find(
        {"members": str(user_id), "active": True},
        {"type": 1, "title": 1},
    )
    return [
        {"id": str(doc["_id"]), "title": doc.get("title", ""),
         "type": doc.get("type", "group"), "scope": scope_for(doc.get("type"))}
        async for doc in cursor
    ]


async def usable_destination(user_id: str, chat_id: str | None) -> dict | None:
    """Resolve a chat the user picked, or None if they may not use it."""
    if not chat_id:
        return None
    try:
        numeric = int(chat_id)
    except (TypeError, ValueError):
        return None

    return await get_chats_collection().find_one(
        {"_id": numeric, "members": str(user_id), "active": True},
        {"type": 1, "title": 1},
    )
