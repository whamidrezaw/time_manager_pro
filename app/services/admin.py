"""Admin control over event limits (Batch 20).

The main admin is ADMIN_CHAT_ID from the environment and nothing else: no
command can make anyone an admin. From the bot they can give one user a limit
of their own, or none at all, and change the base limit and the invite reward
at any time; whatever is not set here falls back to the environment. Unlimited
still stops at the technical ceiling (MAX_EVENTS_PER_USER), which protects the
database. Every change is written to admin_audit, logged, and confirmed.

Everything lives in collections of its own (settings, limit_overrides,
usernames, admin_audit): the referral logic reads whether a user document
exists, so nothing here writes to users.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.config import Settings, get_settings
from app.db import get_database, get_events_collection

logger = logging.getLogger("tm_pro.admin")

UNLIMITED = "unlimited"
DEFAULT = "default"
ADMIN_COMMANDS = frozenset({"/limits", "/limit", "/setbase", "/setbonus", "/setstep"})
# command -> (settings field, smallest value it takes)
RUNTIME = {
    "/setbase": ("event_limit_base", 1),
    "/setbonus": ("referral_bonus", 0),
    "/setstep": ("referral_step", 1),
}
MAX_STEP = 100

USAGE = (
    "Admin commands:\n"
    "/limits - the current settings\n"
    "/limit <@username|id> - one user's limit\n"
    "/limit <@username|id> <number|unlimited|default>\n"
    "/setbase <number|default> - events before any invite\n"
    "/setbonus <number|default> - events per reward\n"
    "/setstep <number|default> - invites per reward"
)


def is_admin(user_id, settings: Settings | None = None) -> bool:
    """Only the ADMIN_CHAT_ID of the environment; there is no other way in."""
    settings = settings or get_settings()
    admin = str(settings.admin_chat_id or "").strip()
    return bool(admin) and user_id is not None and str(user_id) == admin


async def runtime_settings(settings: Settings | None = None) -> Settings:
    """The environment's settings with the admin's changes laid over them."""
    settings = settings or get_settings()
    try:
        doc = await get_database()["settings"].find_one({"_id": "limits"}) or {}
    except Exception:
        logger.exception("runtime limit settings unavailable; using the environment's")
        return settings
    changes = {field: doc[field] for field, _ in RUNTIME.values() if isinstance(doc.get(field), int)}
    return settings.model_copy(update=changes) if changes else settings


async def limit_status(user_id, settings: Settings | None = None) -> dict:
    """Who decides this user's limit: the admin role, a limit of their own,
    or the formula (then limit is None and the caller computes it)."""
    settings = settings or get_settings()
    ceiling = settings.max_events_per_user
    if is_admin(user_id, settings):
        return {"limit": ceiling, "unlimited": True, "source": "admin"}
    try:
        doc = await get_database()["limit_overrides"].find_one({"_id": str(user_id)}) or {}
    except Exception:
        logger.exception("limit override unavailable user_id=%s", user_id)
        doc = {}
    value = doc.get("value")
    if value == UNLIMITED:
        return {"limit": ceiling, "unlimited": True, "source": "unlimited"}
    if isinstance(value, int) and value > 0:
        return {"limit": min(value, ceiling), "unlimited": False, "source": "custom"}
    return {"limit": None, "unlimited": False, "source": "default"}


async def remember_username(user_id, username) -> None:
    """So the admin can name a user as @username; the latest owner of a name wins."""
    if user_id is None or not username:
        return
    try:
        await get_database()["usernames"].update_one(
            {"_id": str(username).lstrip("@").lower()}, {"$set": {"user_id": str(user_id)}}, upsert=True)
    except Exception:
        logger.exception("could not remember the username of user_id=%s", user_id)


async def resolve_user(target: str) -> str | None:
    target = target.strip()
    if re.fullmatch(r"\d{1,20}", target):
        return target
    if re.fullmatch(r"@?[A-Za-z0-9_]{3,32}", target):
        doc = await get_database()["usernames"].find_one({"_id": target.lstrip("@").lower()})
        return doc["user_id"] if doc else None
    return None


async def _audit(admin_id: str, action: str, target: str, before, after) -> None:
    await get_database()["admin_audit"].insert_one({
        "at": datetime.now(timezone.utc), "admin": str(admin_id), "action": action,
        "target": target, "before": before, "after": after,
    })
    logger.info("admin %s %s %s: %r -> %r", admin_id, action, target, before, after)


def _number(text: str, lowest: int, highest: int) -> int | None:
    if not re.fullmatch(r"\d{1,6}", text):
        return None
    value = int(text)
    return value if lowest <= value <= highest else None


async def handle_admin_command(text: str, admin_id: str, settings: Settings | None = None) -> str:
    """One admin command in, one reply out. The caller has checked is_admin."""
    settings = settings or get_settings()
    parts = text.split()
    command = parts[0].split("@")[0].lower() if parts else ""
    args = parts[1:]
    if command == "/limits":
        return await _show_limits(settings)
    if command == "/limit":
        return await _limit(args, admin_id, settings)
    if command in RUNTIME:
        return await _set_runtime(command, args, admin_id, settings)
    return USAGE


async def _show_limits(settings: Settings) -> str:
    runtime = await runtime_settings(settings)
    doc = await get_database()["settings"].find_one({"_id": "limits"}) or {}
    overrides = await get_database()["limit_overrides"].count_documents({})

    def line(label: str, field: str) -> str:
        origin = "set by admin" if field in doc else "default"
        return f"{label}: {getattr(runtime, field)} ({origin})"

    return "\n".join([
        "Event limits",
        line("Base, before any invite", "event_limit_base"),
        line("Invites per reward", "referral_step"),
        line("Events per reward", "referral_bonus"),
        f"Technical ceiling: {settings.max_events_per_user} (MAX_EVENTS_PER_USER on Render)",
        f"Users with a limit of their own: {overrides}",
    ])


async def _limit(args: list[str], admin_id: str, settings: Settings) -> str:
    from app.services.referrals import effective_event_limit  # referrals imports this module

    if not args or len(args) > 2:
        return USAGE
    user_id = await resolve_user(args[0])
    if not user_id:
        return (f"User {args[0]} not found. A @username is known once they have used "
                "the bot or the app; the numeric id always works.")
    used = await get_events_collection().count_documents({"user_id": user_id})
    if len(args) == 1:
        status = await limit_status(user_id, settings)
        limit = await effective_event_limit(user_id, settings)
        shown = "unlimited" if status["unlimited"] else str(limit)
        return f"User {user_id}: {used} events, limit {shown} ({status['source']})."

    choice, ceiling = args[1].lower(), settings.max_events_per_user
    if choice == UNLIMITED:
        value = UNLIMITED
    elif choice == DEFAULT:
        value = None
    else:
        value = _number(choice, 1, ceiling)
        if value is None:
            return f"The limit must be a number from 1 to {ceiling}, unlimited, or default.\n\n{USAGE}"

    overrides = get_database()["limit_overrides"]
    before = (await overrides.find_one({"_id": user_id}) or {}).get("value")
    if value is None:
        await overrides.delete_one({"_id": user_id})
    else:
        await overrides.update_one({"_id": user_id}, {"$set": {"value": value}}, upsert=True)
    await _audit(admin_id, "limit", user_id, before, value)

    limit = await effective_event_limit(user_id, settings)
    reply = f"Done: user {user_id} now has limit {'unlimited' if value == UNLIMITED else limit}"
    reply += " (the default formula)." if value is None else "."
    if isinstance(value, int) and used > value:
        reply += (f"\nNote: they already have {used} events, more than {value}. "
                  "Those stay; new ones wait until they are under it.")
    return reply


async def _set_runtime(command: str, args: list[str], admin_id: str, settings: Settings) -> str:
    field, lowest = RUNTIME[command]
    if len(args) != 1:
        return USAGE
    highest = MAX_STEP if field == "referral_step" else settings.max_events_per_user
    stored = get_database()["settings"]
    before = (await stored.find_one({"_id": "limits"}) or {}).get(field)
    if args[0].lower() == DEFAULT:
        await stored.update_one({"_id": "limits"}, {"$unset": {field: ""}}, upsert=True)
        after = None
    else:
        after = _number(args[0], lowest, highest)
        if after is None:
            return f"{command} takes a number from {lowest} to {highest}, or default.\n\n{USAGE}"
        await stored.update_one({"_id": "limits"}, {"$set": {field: after}}, upsert=True)
    await _audit(admin_id, command.lstrip("/"), field, before, after)
    runtime = await runtime_settings(settings)
    return f"Done: {field} is now {getattr(runtime, field)}" + (" (the default)." if after is None else ".")
