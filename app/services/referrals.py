from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.config import Settings, get_settings
from app.db import get_events_collection, get_users_collection

logger = logging.getLogger("tm_pro.referrals")

# No 0/O/1/I/L: the code is meant to survive being read aloud or retyped.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 8

# What a share link carries: "r_7KQ2M9XA". Telegram allows [A-Za-z0-9_-] in a
# start payload, so the prefix costs two characters and buys us the freedom to
# add other payload kinds later (Batch 12b needs "e_<event_id>").
REF_PREFIX = "r_"

STATUS_PENDING = "pending"
STATUS_VALID = "valid"


# ── Pure helpers (no I/O — these are what the tests pin down) ─────────────

def generate_ref_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def normalize_code(raw: str | None) -> str | None:
    """Accept a code however it arrives and return the canonical form.

    Telegram upper/lower-cases nothing for us, and a user may well paste the
    whole payload, so "r_7kq2m9xa", "R_7KQ2M9XA" and "7KQ2M9XA" all resolve to
    the same code. Anything that is not in the alphabet is rejected outright
    rather than trimmed, because a half-valid code is a lookup that quietly
    credits the wrong person.
    """
    value = (raw or "").strip()
    if not value:
        return None

    lowered = value.lower()
    if lowered.startswith(REF_PREFIX):
        value = value[len(REF_PREFIX):]
    elif lowered.startswith("ref_"):
        value = value[4:]

    value = value.upper()
    if len(value) != CODE_LENGTH:
        return None
    if any(char not in _CODE_ALPHABET for char in value):
        return None
    return value


def parse_ref_payload(payload: str | None) -> str | None:
    """The deep-link payload -> a referral code, or None if it is not one."""
    return normalize_code(payload)


def compute_event_limit(valid_invites: int, settings: Settings | None = None) -> int:
    """base + bonus for every completed step, never above the hard ceiling."""
    settings = settings or get_settings()

    step = max(settings.referral_step, 1)
    steps_done = max(valid_invites, 0) // step
    limit = settings.event_limit_base + steps_done * settings.referral_bonus
    return min(limit, settings.max_events_per_user)


def invites_to_next_bonus(valid_invites: int, settings: Settings | None = None) -> int:
    settings = settings or get_settings()

    step = max(settings.referral_step, 1)
    return step - (max(valid_invites, 0) % step)


def build_invite_link(code: str, settings: Settings | None = None) -> str:
    """The canonical share link.

    startapp (not start) so a tap lands the invitee straight in the Mini App
    instead of in an empty chat: one less step between the link and the first
    event, which is the only moment the invite actually counts.
    """
    settings = settings or get_settings()

    return (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={REF_PREFIX}{code}"
    )


# ── Stored state ─────────────────────────────────────────────────────────

async def ensure_ref_code(user_id: str, attempts: int = 5) -> str:
    """Return this user's code, minting one on first use.

    The retry loop is for the unique index on ref_code: 31^8 is a big space,
    but "unlikely" is not "impossible", and a collision must cost a retry
    rather than a 500.
    """
    users = get_users_collection()

    existing = await users.find_one({"_id": user_id}, {"ref_code": 1})
    if existing and existing.get("ref_code"):
        return existing["ref_code"]

    now = datetime.now(timezone.utc)
    for _ in range(attempts):
        code = generate_ref_code()
        try:
            await users.update_one(
                {"_id": user_id},
                {
                    "$set": {"ref_code": code, "updated_at": now},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            return code
        except DuplicateKeyError:
            continue

    raise RuntimeError("could not allocate a unique referral code")


async def resolve_code(code: str) -> str | None:
    users = get_users_collection()

    owner = await users.find_one({"ref_code": code}, {"_id": 1})
    return owner["_id"] if owner else None


async def attach_referrer(invitee_id: str, code: str) -> bool:
    """Record who invited this user. First touch wins, for good.

    Four things are refused here, and each one is a way the reward could
    otherwise be farmed: an unknown code, your own code, a user who already
    has a referrer, and a user who has already created events (an established
    account cannot be retro-attributed by opening someone's link).
    """
    invitee_id = str(invitee_id)

    normalized = normalize_code(code)
    if not normalized:
        return False

    referrer_id = await resolve_code(normalized)
    if not referrer_id or str(referrer_id) == invitee_id:
        return False

    users = get_users_collection()
    events = get_events_collection()

    existing = await users.find_one(
        {"_id": invitee_id},
        {"referred_by": 1, "first_event_at": 1},
    )
    if existing and (existing.get("referred_by") or existing.get("first_event_at")):
        return False

    if await events.count_documents({"user_id": invitee_id}, limit=1):
        return False

    now = datetime.now(timezone.utc)
    try:
        result = await users.update_one(
            # referred_by in the filter, not just in the read above: two taps
            # on two different links in the same second must not race into a
            # swap. The filter losing its match then makes Mongo attempt an
            # insert on an _id that exists, which is what the except catches.
            {"_id": invitee_id, "referred_by": {"$exists": False}},
            {
                "$set": {
                    "referred_by": str(referrer_id),
                    "referral_status": STATUS_PENDING,
                    "referred_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False

    attached = bool(result.modified_count or result.upserted_id)
    if attached:
        logger.info("referral pending invitee=%s referrer=%s", invitee_id, referrer_id)
    return attached


async def count_valid_invites(user_id: str) -> int:
    users = get_users_collection()

    return await users.count_documents(
        {"referred_by": str(user_id), "referral_status": STATUS_VALID}
    )


async def count_pending_invites(user_id: str) -> int:
    users = get_users_collection()

    return await users.count_documents(
        {"referred_by": str(user_id), "referral_status": STATUS_PENDING}
    )


async def effective_event_limit(user_id: str, settings: Settings | None = None) -> int:
    """The live limit for one user.

    Derived on every call rather than cached on the user document, for the
    same reason occurrences are computed at request time: a stored copy is a
    second source of truth, and the two drift the first time a write fails
    halfway.
    """
    settings = settings or get_settings()

    try:
        valid = await count_valid_invites(user_id)
    except Exception:
        # A limit lookup must never be what stops someone saving an event.
        logger.exception("valid-invite count failed user_id=%s", user_id)
        valid = 0

    return compute_event_limit(valid, settings)


async def activate_referral_if_first_event(user_id: str) -> tuple[str, int] | None:
    """Mark this user as activated; promote a pending invite to a valid one.

    Returns (referrer_id, valid_count) only when that promotion completed a
    step and therefore earned the referrer a bonus — the caller uses that to
    decide whether a notification is worth sending.
    """
    user_id = str(user_id)
    users = get_users_collection()
    now = datetime.now(timezone.utc)

    # The filter carries the idempotency: only the very first event of this
    # user matches, so the second one cannot count the same invite twice.
    before = await users.find_one_and_update(
        {"_id": user_id, "first_event_at": {"$exists": False}},
        {"$set": {"first_event_at": now, "updated_at": now}},
    )
    if before is None:
        return None

    referrer_id = before.get("referred_by")
    if not referrer_id or before.get("referral_status") != STATUS_PENDING:
        return None

    promoted = await users.update_one(
        {"_id": user_id, "referral_status": STATUS_PENDING},
        {"$set": {"referral_status": STATUS_VALID, "referral_valid_at": now}},
    )
    if not promoted.modified_count:
        return None

    valid_count = await count_valid_invites(referrer_id)
    logger.info(
        "referral valid invitee=%s referrer=%s total=%s", user_id, referrer_id, valid_count
    )

    settings = get_settings()
    step = max(settings.referral_step, 1)
    at_cap = compute_event_limit(valid_count, settings) >= settings.max_events_per_user

    if valid_count % step == 0 and not at_cap:
        return str(referrer_id), valid_count
    return None


async def referral_overview(user_id: str, settings: Settings | None = None) -> dict:
    """Everything the invite screen needs, in one round trip."""
    settings = settings or get_settings()

    code = await ensure_ref_code(user_id)
    valid = await count_valid_invites(user_id)
    pending = await count_pending_invites(user_id)
    used = await get_events_collection().count_documents({"user_id": user_id})
    limit = compute_event_limit(valid, settings)

    return {
        "success": True,
        "code": code,
        "link": build_invite_link(code, settings),
        "used": used,
        "limit": limit,
        "cap": settings.max_events_per_user,
        "valid_invites": valid,
        "pending_invites": pending,
        "step": max(settings.referral_step, 1),
        "bonus": settings.referral_bonus,
        "invites_to_next": invites_to_next_bonus(valid, settings),
        "at_cap": limit >= settings.max_events_per_user,
    }


async def notify_referrer_bonus(referrer_id: str, valid_count: int) -> None:
    """Tell the referrer their limit went up. Best effort, never fatal.

    Deliberately only fired on the step that actually grants the bonus. A
    message after every single invite would be three times the noise for the
    same information, and the bot has no other reason to write unprompted.
    """
    from telegram import Bot

    from app.routes.telegram import build_open_app_keyboard
    from app.utils.i18n import t

    settings = get_settings()
    limit = compute_event_limit(valid_count, settings)

    language = "en"
    try:
        recent = await get_events_collection().find_one(
            {"user_id": str(referrer_id)},
            {"lang": 1},
            sort=[("created_at", -1)],
        )
        if recent and recent.get("lang") in ("en", "fa"):
            language = recent["lang"]
    except Exception:
        logger.exception("referrer language lookup failed user_id=%s", referrer_id)

    text = t("referral_bonus_granted", language).format(limit=limit)

    try:
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=str(referrer_id),
                text=text,
                parse_mode="HTML",
                reply_markup=build_open_app_keyboard(settings, language),
            )
    except Exception:
        # Blocked the bot, deleted the account, Telegram having a bad minute —
        # none of it should surface as an error on the invitee's save.
        logger.exception("referral bonus notice failed referrer=%s", referrer_id)
