#!/usr/bin/env python3
"""
apply_batch12a.py — TimeManager Pro, Batch 12a (Referral & dynamic event limit)

Run once from the repository root:

    python apply_batch12a.py          # apply
    python apply_batch12a.py --check  # dry run, writes nothing

Safe to run twice: every step detects its own marker and skips.
Nothing is written unless *all* steps resolve, so a failed anchor leaves the
working tree untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRY_RUN = "--check" in sys.argv

# path -> new content, filled by the steps below and flushed at the end
PENDING: dict[Path, str] = {}
LOG: list[tuple[str, str]] = []      # (status, message)
FAILED = False


def _note(status: str, message: str) -> None:
    LOG.append((status, message))


def _fail(message: str) -> None:
    global FAILED
    FAILED = True
    _note("FAIL", message)


def _current(path: Path) -> str | None:
    if path in PENDING:
        return PENDING[path]
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def patch(rel: str, old: str, new: str, marker: str, label: str) -> None:
    """Replace `old` with `new` exactly once. `marker` proves it already ran."""
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found — are you in the repository root?")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    count = text.count(old)
    if count == 0:
        _fail(f"{rel}: anchor for '{label}' not found — file has changed since Batch 11")
        return
    if count > 1:
        _fail(f"{rel}: anchor for '{label}' matches {count} times — too ambiguous to patch")
        return

    PENDING[path] = text.replace(old, new, 1)
    _note(" OK ", f"{rel}: {label}")


def append(rel: str, addition: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    PENDING[path] = text.rstrip("\n") + "\n" + addition
    _note(" OK ", f"{rel}: {label}")


def create(rel: str, content: str, label: str) -> None:
    path = ROOT / rel
    PENDING[path] = content
    _note(" OK " if not path.exists() else "OVER", f"{rel}: {label}")


def flush() -> None:
    for path, content in PENDING.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 1. Configuration
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/config.py",
    old='    max_events_per_user: int = Field(default=500, alias="MAX_EVENTS_PER_USER")\n',
    new=(
        '    max_events_per_user: int = Field(default=500, alias="MAX_EVENTS_PER_USER")\n'
        "\n"
        "    # Batch 12a. max_events_per_user is now the hard ceiling nobody passes;\n"
        "    # what a given user may actually store is base + bonus per referral step,\n"
        "    # computed in app/services/referrals.py.\n"
        '    event_limit_base: int = Field(default=20, alias="EVENT_LIMIT_BASE")\n'
        '    referral_step: int = Field(default=3, alias="REFERRAL_STEP")\n'
        '    referral_bonus: int = Field(default=20, alias="REFERRAL_BONUS")\n'
    ),
    marker="event_limit_base",
    label="referral settings",
)

patch(
    ".env.example",
    old="MAX_EVENTS_PER_USER=500\n",
    new=(
        "# Hard ceiling — no user can ever pass this, however many invites they send\n"
        "MAX_EVENTS_PER_USER=500\n"
        "# Batch 12a — referral rewards\n"
        "# Every user starts here...\n"
        "EVENT_LIMIT_BASE=20\n"
        "# ...and gains REFERRAL_BONUS more for every REFERRAL_STEP valid invites\n"
        "REFERRAL_STEP=3\n"
        "REFERRAL_BONUS=20\n"
    ),
    marker="EVENT_LIMIT_BASE",
    label="referral env vars",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Indexes
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/db.py",
    old='    rate_limits = get_database()["rate_limits"]\n',
    new=(
        "    users = get_users_collection()\n"
        "\n"
        "    # sparse: user documents written before Batch 12a carry no ref_code,\n"
        "    # and a plain unique index would reject all but the first of them.\n"
        '    await users.create_index("ref_code", unique=True, sparse=True)\n'
        '    await users.create_index([("referred_by", 1), ("referral_status", 1)])\n'
        "\n"
        '    rate_limits = get_database()["rate_limits"]\n'
    ),
    marker='create_index("ref_code"',
    label="users indexes for referral",
)

# ══════════════════════════════════════════════════════════════════════
# 3. New service — app/services/referrals.py
# ══════════════════════════════════════════════════════════════════════

REFERRALS_SERVICE = '''from __future__ import annotations

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
'''

create("app/services/referrals.py", REFERRALS_SERVICE, "new referral service")

# ══════════════════════════════════════════════════════════════════════
# 4. Event creation — dynamic limit + activation
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/events.py",
    old=(
        '    count = await events_coll.count_documents({"user_id": user_id})\n'
        "    if count >= settings.max_events_per_user:\n"
        '        raise HTTPException(status_code=400, detail="EVENT_LIMIT_REACHED")\n'
    ),
    new=(
        "    # Imported here rather than at module scope to keep this module's\n"
        "    # import graph flat, the same way check_rate_limit_mongo pulls in\n"
        "    # get_database. The limit is derived per user now, not a constant.\n"
        "    from app.services.referrals import effective_event_limit\n"
        "\n"
        '    count = await events_coll.count_documents({"user_id": user_id})\n'
        "    if count >= await effective_event_limit(user_id, settings):\n"
        '        raise HTTPException(status_code=400, detail="EVENT_LIMIT_REACHED")\n'
    ),
    marker="effective_event_limit",
    label="dynamic per-user event limit",
)

patch(
    "app/services/events.py",
    old=(
        "    result = await events_coll.insert_one(event_data)\n"
        '    logger.info("event inserted user_id=%s event_id=%s", user_id, result.inserted_id)\n'
    ),
    new=(
        "    result = await events_coll.insert_one(event_data)\n"
        '    logger.info("event inserted user_id=%s event_id=%s", user_id, result.inserted_id)\n'
        "\n"
        "    # Batch 12a: an invite only counts once the invited person has actually\n"
        "    # made something. After the insert, never before it — a save that fails\n"
        "    # must not be able to award anyone a bonus.\n"
        "    try:\n"
        "        from app.services.referrals import (\n"
        "            activate_referral_if_first_event,\n"
        "            notify_referrer_bonus,\n"
        "        )\n"
        "\n"
        "        earned = await activate_referral_if_first_event(user_id)\n"
        "        if earned:\n"
        "            await notify_referrer_bonus(*earned)\n"
        "    except Exception:\n"
        '        logger.exception("referral activation failed user_id=%s", user_id)\n'
    ),
    marker="activate_referral_if_first_event",
    label="referral activation on first event",
)

# ══════════════════════════════════════════════════════════════════════
# 5. Bot side — /start deep link
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/routes/telegram.py",
    old='    return parts[0].split("@", 1)[0].lower()\n',
    new=(
        '    return parts[0].split("@", 1)[0].lower()\n'
        "\n"
        "\n"
        "def parse_start_payload(text: str) -> str:\n"
        '    """The argument after /start, or \'\' when there is none.\n'
        "\n"
        "    Separate from parse_command on purpose: that function normalises a\n"
        "    message down to the bare command and its tests guarantee the payload is\n"
        "    dropped. Reading the payload is a different question, so it gets its own\n"
        "    function instead of a second return value nobody else wants.\n"
        '    """\n'
        '    parts = (text or "").strip().split(maxsplit=1)\n'
        '    if len(parts) < 2 or not parts[0].startswith("/"):\n'
        '        return ""\n'
        "    return parts[1].strip()\n"
    ),
    marker="def parse_start_payload",
    label="start payload parser",
)

patch(
    "app/routes/telegram.py",
    old='    if command == "/start":\n        key = "start"\n',
    new=(
        '    if command == "/start":\n'
        '        key = "start"\n'
        "        await _attach_referral_from_start(message)\n"
    ),
    marker="_attach_referral_from_start(message)",
    label="referral capture on /start",
)

append(
    "app/routes/telegram.py",
    addition='''

async def _attach_referral_from_start(message) -> None:
    """Credit whoever's link brought this person here. Best effort.

    A failure here is invisible to the user by design: they came to use the
    bot, and the welcome message matters more than the bookkeeping.
    """
    code = parse_start_payload(message.text or "")
    if not code:
        return

    try:
        from app.services.referrals import attach_referrer, parse_ref_payload

        normalized = parse_ref_payload(code)
        if normalized:
            await attach_referrer(str(message.from_user.id), normalized)
    except Exception:
        logger.exception("referral attach failed chat_id=%s", message.chat_id)
''',
    # Distinct from the call site inserted just above, which contains the same
    # name — matching on that would skip the definition and leave a NameError.
    marker="async def _attach_referral_from_start",
    label="referral capture helper",
)

# ══════════════════════════════════════════════════════════════════════
# 6. Bot copy
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/utils/i18n.py",
    old='    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},\n',
    new=(
        '    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},\n'
        '    "referral_bonus_granted": {\n'
        '        "en": (\n'
        '            "🎁 <b>Your limit just went up!</b>\\n\\n"\n'
        '            "Someone you invited saved their first event. "\n'
        '            "You can now keep up to <b>{limit}</b> events."\n'
        "        ),\n"
        '        "fa": (\n'
        '            "🎁 <b>سقف شما بالا رفت!</b>\\n\\n"\n'
        '            "کسی که دعوت کرده بودید اولین رویدادش را ذخیره کرد. "\n'
        '            "حالا می‌توانید تا <b>{limit}</b> رویداد داشته باشید."\n'
        "        ),\n"
        "    },\n"
    ),
    marker="referral_bonus_granted",
    label="bonus notification copy",
)

# ══════════════════════════════════════════════════════════════════════
# 7. API route
# ══════════════════════════════════════════════════════════════════════

REFERRAL_ROUTE = '''from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, validate_init_data
from app.services.referrals import (
    attach_referrer,
    parse_ref_payload,
    referral_overview,
)

router = APIRouter(prefix="/api", tags=["referral"])
logger = logging.getLogger("tm_pro.referral")


@router.post("/referral")
async def api_referral(request: Request, payload: InitDataPayload) -> dict:
    """The invite screen's only endpoint: state in, full picture out.

    This is also the second half of the attribution path. A tap on a share
    link opens the Mini App directly, and Telegram then hands the payload over
    as start_param inside initData — signed, so it is as trustworthy as the
    user id next to it. The /start branch in routes/telegram.py covers the
    other route in, for anyone who lands in the chat first.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)
    user_id = auth["user_id"]

    code = parse_ref_payload((auth.get("raw") or {}).get("start_param"))
    if code:
        try:
            await attach_referrer(user_id, code)
        except Exception:
            logger.exception("referral attach failed user_id=%s", user_id)

    return await referral_overview(user_id, settings)
'''

create("app/routes/referral.py", REFERRAL_ROUTE, "new referral endpoint")

patch(
    "app/main.py",
    old="from app.routes.health import router as health_router\n",
    new=(
        "from app.routes.health import router as health_router\n"
        "from app.routes.referral import router as referral_router\n"
    ),
    marker="referral_router",
    label="import referral router",
)

patch(
    "app/main.py",
    old="app.include_router(events_router)\n",
    new=(
        "app.include_router(events_router)\n"
        "app.include_router(referral_router)\n"
    ),
    marker="include_router(referral_router)",
    label="register referral router",
)

patch(
    "app/routes/web.py",
    old='    for name in ("style.css", "app.js"):\n',
    new='    for name in ("style.css", "app.js", "referral.js"):\n',
    marker='"referral.js"',
    label="cache-bust the new script",
)

# ══════════════════════════════════════════════════════════════════════
# 8. Mini App — load the new script
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old='  <script src="/static/app.js?v={{ asset_version }}" defer></script>\n',
    new=(
        '  <script src="/static/app.js?v={{ asset_version }}" defer></script>\n'
        '  <script src="/static/referral.js?v={{ asset_version }}" defer></script>\n'
    ),
    marker="referral.js",
    label="load referral.js",
)

# The limit is no longer a constant, so the message must stop naming one.
patch(
    "static/app.js",
    old='      EVENT_LIMIT_REACHED:    "You have reached the maximum number of events (500).",\n',
    new='      EVENT_LIMIT_REACHED:    "You have reached your event limit. Invite friends to raise it.",\n',
    marker="Invite friends to raise it.",
    label="limit message (English)",
)

patch(
    "static/app.js",
    old='      "You have reached the maximum number of events (500).": "به حداکثر تعداد رویداد رسیده‌اید (۵۰۰).",\n',
    new='      "You have reached your event limit. Invite friends to raise it.": "به سقف رویدادهایتان رسیده‌اید. با دعوت دوستان آن را بالا ببرید.",\n',
    marker="با دعوت دوستان آن را بالا ببرید",
    label="limit message (Persian)",
)

# ══════════════════════════════════════════════════════════════════════
# 9. Mini App — the invite screen
# ══════════════════════════════════════════════════════════════════════

REFERRAL_JS = r'''/* ──────────────────────────────────────────────────────────────
   referral.js — Batch 12a, the invite screen.

   Deliberately standalone rather than folded into app.js: everything it
   needs from Telegram it can read itself, and every element it shows it
   builds itself. Nothing in app.js has to change for this to work, and
   nothing here can break the event list if it throws.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var NEAR_LIMIT_RATIO = 0.8;

  var FA = {
    "Invite friends": "دعوت از دوستان",
    "Invite a friend": "دعوت از دوستان",
    "Your invite link": "لینک دعوت شما",
    "Share link": "ارسال لینک",
    "Copy link": "کپی لینک",
    "Copied": "کپی شد",
    "Close": "بستن",
    "events used": "رویداد استفاده شده",
    "Every {step} friends who join and save their first event raise your limit by {bonus} events.":
      "به ازای هر {step} دوستی که وارد شود و اولین رویدادش را ذخیره کند، {bonus} رویداد به سقف شما اضافه می‌شود.",
    "{n} more to go for +{bonus} events": "{n} دعوت دیگر تا +{bonus} رویداد",
    "You have reached the highest limit. Thank you!": "به بالاترین سقف رسیده‌اید. ممنون از شما!",
    "{valid} joined": "{valid} نفر پیوسته‌اند",
    "{pending} on the way": "{pending} نفر در راه",
    "Running out of space": "جا دارد تمام می‌شود",
    "You've used {used} of your {limit} events. Invite friends to get more.":
      "{used} از {limit} رویداد شما استفاده شده است. با دعوت دوستان سقف را بالا ببرید.",
    "Invite now": "همین حالا دعوت کن",
    "Join me on TimeManager Pro — never miss a birthday, meeting or deadline again.":
      "به تایم‌منیجر پرو بیا — دیگر هیچ تولد، جلسه یا مهلتی را از دست نده.",
    "Could not load your invite link.": "لینک دعوت بارگذاری نشد.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text, vars) {
    var out = isFa && FA[text] ? FA[text] : text;
    if (vars) {
      Object.keys(vars).forEach(function (key) {
        out = out.split("{" + key + "}").join(vars[key]);
      });
    }
    return out;
  }

  // Persian digits, so the numbers match the rest of the interface.
  function num(value) {
    var text = String(value);
    if (!isFa) return text;
    return text.replace(/[0-9]/g, function (d) {
      return "۰۱۲۳۴۵۶۷۸۹"[Number(d)];
    });
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  var state = null;
  var els = {};

  async function fetchState() {
    var response = await fetch("/api/referral", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: (tg && tg.initData) || "" }),
    });
    if (!response.ok) throw new Error("REFERRAL_FAILED");
    return response.json();
  }

  /* ── Markup ─────────────────────────────────────────── */

  function icon() {
    return (
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
      ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/>' +
      '<line x1="12" y1="22" x2="12" y2="7"/>' +
      '<path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/>' +
      '<path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>'
    );
  }

  function mountHeaderButton() {
    var header = document.querySelector(".app-header");
    if (!header || document.getElementById("refOpenBtn")) return;

    var button = document.createElement("button");
    button.type = "button";
    button.id = "refOpenBtn";
    button.className = "icon-btn ref-open-btn";
    button.setAttribute("aria-label", t("Invite friends"));
    button.innerHTML = icon() + '<span class="ref-dot" id="refDot" hidden></span>';
    button.addEventListener("click", open);

    // The header is a two-child flexbox with space-between; dropping a third
    // child straight into it would push the existing action off its edge.
    // Grouping the buttons keeps the layout exactly as it was.
    var group = document.createElement("div");
    group.className = "ref-header-actions";
    header.appendChild(group);
    group.appendChild(button);

    Array.prototype.slice
      .call(header.children)
      .filter(function (child) {
        return child !== group && child.classList.contains("icon-btn");
      })
      .forEach(function (child) {
        group.appendChild(child);
      });
  }

  function buildSheet() {
    if (els.overlay) return;

    var overlay = document.createElement("div");
    overlay.className = "ref-overlay";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML =
      '<div class="ref-dialog" role="dialog" aria-modal="true" aria-labelledby="refTitle">' +
        '<div class="ref-handle" aria-hidden="true"></div>' +
        '<div class="ref-head">' +
          '<h2 class="ref-title" id="refTitle">' + t("Invite friends") + "</h2>" +
          '<button type="button" class="icon-btn ref-close" id="refCloseBtn" aria-label="' +
            t("Close") + '">✕</button>' +
        "</div>" +
        '<p class="ref-lead" id="refLead"></p>' +
        '<div class="ref-meter">' +
          '<div class="ref-meter-head">' +
            '<strong id="refUsage"></strong><span id="refNext"></span>' +
          "</div>" +
          '<div class="ref-bar"><div class="ref-bar-fill" id="refBarFill"></div></div>' +
          '<div class="ref-chips"><span class="ref-chip" id="refValid"></span>' +
            '<span class="ref-chip ref-chip-muted" id="refPending"></span></div>' +
        "</div>" +
        '<label class="ref-link-label" for="refLink">' + t("Your invite link") + "</label>" +
        '<input class="ref-link" id="refLink" readonly />' +
        '<div class="ref-actions">' +
          '<button type="button" class="btn-secondary" id="refCopyBtn">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="refShareBtn">' + t("Share link") + "</button>" +
        "</div>" +
      "</div>";

    document.body.appendChild(overlay);

    els.overlay = overlay;
    els.lead = overlay.querySelector("#refLead");
    els.usage = overlay.querySelector("#refUsage");
    els.next = overlay.querySelector("#refNext");
    els.fill = overlay.querySelector("#refBarFill");
    els.valid = overlay.querySelector("#refValid");
    els.pending = overlay.querySelector("#refPending");
    els.link = overlay.querySelector("#refLink");
    els.copy = overlay.querySelector("#refCopyBtn");
    els.share = overlay.querySelector("#refShareBtn");

    if (isFa) overlay.setAttribute("dir", "rtl");

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    overlay.querySelector("#refCloseBtn").addEventListener("click", close);
    els.copy.addEventListener("click", copyLink);
    els.share.addEventListener("click", shareLink);
  }

  /* ── Rendering ──────────────────────────────────────── */

  function render() {
    if (!state || !els.overlay) return;

    els.lead.textContent = t(
      "Every {step} friends who join and save their first event raise your limit by {bonus} events.",
      { step: num(state.step), bonus: num(state.bonus) }
    );

    els.usage.textContent = num(state.used) + " / " + num(state.limit) + " " + t("events used");
    els.next.textContent = state.at_cap
      ? t("You have reached the highest limit. Thank you!")
      : t("{n} more to go for +{bonus} events", {
          n: num(state.invites_to_next),
          bonus: num(state.bonus),
        });

    var ratio = state.limit > 0 ? Math.min(state.used / state.limit, 1) : 0;
    els.fill.style.width = (ratio * 100).toFixed(1) + "%";
    els.fill.classList.toggle("is-hot", ratio >= NEAR_LIMIT_RATIO);

    els.valid.textContent = t("{valid} joined", { valid: num(state.valid_invites) });
    els.pending.textContent = t("{pending} on the way", { pending: num(state.pending_invites) });
    els.pending.hidden = !state.pending_invites;

    els.link.value = state.link;
  }

  function renderNudge() {
    var main = document.querySelector(".app-main");
    var existing = document.getElementById("refNudge");
    if (!main || !state) return;

    var ratio = state.limit > 0 ? state.used / state.limit : 0;
    if (state.at_cap || ratio < NEAR_LIMIT_RATIO) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return;

    var card = document.createElement("section");
    card.className = "ref-nudge";
    card.id = "refNudge";
    card.innerHTML =
      '<div class="ref-nudge-body"><strong>' + t("Running out of space") + "</strong>" +
      "<p>" +
      t("You've used {used} of your {limit} events. Invite friends to get more.", {
        used: num(state.used),
        limit: num(state.limit),
      }) +
      "</p></div>" +
      '<button type="button" class="btn-primary ref-nudge-btn">' + t("Invite now") + "</button>";

    card.querySelector("button").addEventListener("click", open);

    var toolbar = main.querySelector(".toolbar");
    if (toolbar) main.insertBefore(card, toolbar);
    else main.appendChild(card);
  }

  /* ── Actions ────────────────────────────────────────── */

  function open() {
    haptic();
    buildSheet();
    render();
    els.overlay.hidden = false;
    els.overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(function () {
      els.overlay.classList.add("is-open");
    });
    refresh();
  }

  function close() {
    if (!els.overlay) return;
    els.overlay.classList.remove("is-open");
    els.overlay.setAttribute("aria-hidden", "true");
    setTimeout(function () {
      els.overlay.hidden = true;
    }, 200);
  }

  function flash(button, text) {
    var original = button.textContent;
    button.textContent = text;
    setTimeout(function () {
      button.textContent = original;
    }, 1500);
  }

  function copyLink() {
    if (!state) return;
    haptic("success");

    var done = function () {
      flash(els.copy, t("Copied"));
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(state.link).then(done, fallbackCopy);
    } else {
      fallbackCopy();
    }

    function fallbackCopy() {
      try {
        els.link.removeAttribute("readonly");
        els.link.select();
        document.execCommand("copy");
        els.link.setAttribute("readonly", "readonly");
        done();
      } catch (_) {}
    }
  }

  function shareLink() {
    if (!state) return;
    haptic("success");

    var text = t(
      "Join me on TimeManager Pro — never miss a birthday, meeting or deadline again."
    );
    var url =
      "https://t.me/share/url?url=" +
      encodeURIComponent(state.link) +
      "&text=" +
      encodeURIComponent(text);

    if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  /* ── Boot ───────────────────────────────────────────── */

  async function refresh() {
    try {
      state = await fetchState();
      render();
      renderNudge();
    } catch (error) {
      if (els.lead) els.lead.textContent = t("Could not load your invite link.");
    }
  }

  function init() {
    mountHeaderButton();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
'''

create("static/referral.js", REFERRAL_JS, "invite screen")

REFERRAL_CSS = r'''
/* ── Batch 12a — Invite & referral ───────────────────── */
.ref-header-actions { display: flex; align-items: center; gap: 8px; }

.ref-open-btn { position: relative; }

.ref-nudge {
  display: flex; align-items: center; gap: 14px;
  padding: 16px 18px;
  border-radius: var(--r-lg);
  background: var(--surface);
  border: 1.5px solid rgba(245, 158, 11, 0.35);
  box-shadow: var(--shadow-card);
}
.ref-nudge-body { flex: 1; min-width: 0; }
.ref-nudge-body strong { display: block; font-size: 0.95rem; font-weight: 800; }
.ref-nudge-body p { margin: 4px 0 0; font-size: 0.83rem; color: var(--text-muted); line-height: 1.5; }
.ref-nudge-btn { min-height: 42px; padding: 0 16px; font-size: 0.85rem; flex-shrink: 0; }

.ref-overlay {
  position: fixed; inset: 0; z-index: 60;
  display: flex; align-items: flex-end; justify-content: center;
  background: rgba(10, 12, 30, 0.5);
  backdrop-filter: blur(3px);
  opacity: 0;
  transition: opacity 200ms ease;
}
.ref-overlay.is-open { opacity: 1; }

.ref-dialog {
  width: min(100%, var(--app-max, 560px));
  max-height: 92svh; overflow-y: auto;
  padding: 10px 20px calc(24px + env(safe-area-inset-bottom, 0px));
  background: var(--surface);
  border-radius: var(--r-xl) var(--r-xl) 0 0;
  border: 1px solid var(--border);
  transform: translateY(16px);
  transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
}
.ref-overlay.is-open .ref-dialog { transform: translateY(0); }

.ref-handle {
  width: 40px; height: 4px; margin: 6px auto 14px;
  border-radius: var(--r-pill); background: var(--border);
}

.ref-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.ref-title { margin: 0; font-size: 1.05rem; font-weight: 800; }
.ref-close { width: 36px; height: 36px; font-size: 0.9rem; }

.ref-lead {
  margin: 8px 0 18px;
  font-size: 0.86rem; line-height: 1.6; color: var(--text-muted);
}

.ref-meter {
  padding: 16px; margin-bottom: 18px;
  border-radius: var(--r-md);
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.ref-meter-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px; margin-bottom: 10px;
}
.ref-meter-head strong { font-size: 0.92rem; font-weight: 800; }
.ref-meter-head span { font-size: 0.78rem; color: var(--text-muted); text-align: end; }

.ref-bar {
  height: 8px; border-radius: var(--r-pill);
  background: var(--border); overflow: hidden;
}
.ref-bar-fill {
  height: 100%; width: 0%;
  border-radius: var(--r-pill);
  background: var(--brand-grad);
  transition: width 400ms cubic-bezier(0.22, 1, 0.36, 1);
}
.ref-bar-fill.is-hot { background: linear-gradient(90deg, #f59e0b, #ef4444); }

.ref-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.ref-chip {
  padding: 4px 12px; border-radius: var(--r-pill);
  background: rgba(91, 108, 248, 0.1); color: var(--brand);
  font-size: 0.76rem; font-weight: 700;
}
.ref-chip-muted { background: var(--border); color: var(--text-muted); }

.ref-link-label {
  display: block; margin-bottom: 8px;
  font-size: 0.78rem; font-weight: 700; color: var(--text-2);
}
.ref-link {
  width: 100%; min-height: 48px; padding: 0 14px;
  border: 1.5px solid var(--border); border-radius: var(--r-md);
  background: var(--surface-2); color: var(--text-2);
  font-size: 0.82rem;
  direction: ltr; text-align: start;
}

.ref-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }

@media (prefers-reduced-motion: reduce) {
  .ref-overlay, .ref-dialog, .ref-bar-fill { transition: none; }
}
'''

append("static/style.css", REFERRAL_CSS, "Batch 12a — Invite & referral", "invite styles")

# ══════════════════════════════════════════════════════════════════════
# 10. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routes.telegram import parse_command, parse_start_payload
from app.services.referrals import (
    CODE_LENGTH,
    STATUS_PENDING,
    STATUS_VALID,
    build_invite_link,
    compute_event_limit,
    generate_ref_code,
    invites_to_next_bonus,
    normalize_code,
    parse_ref_payload,
)

# The reward maths must not move when someone edits the .env, so every test
# that asserts a number brings its own settings rather than reading the real
# ones. The defaults here are the product decision: 20, +20 per 3, cap 500.
SETTINGS = SimpleNamespace(
    event_limit_base=20,
    referral_step=3,
    referral_bonus=20,
    max_events_per_user=500,
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


# ── Codes ────────────────────────────────────────────────────────────

def test_generated_codes_are_unambiguous_and_unique() -> None:
    codes = {generate_ref_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert len(code) == CODE_LENGTH
        # 0/O/1/I/L are excluded so a code survives being read out loud.
        assert not set(code) & set("01OIL")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("r_7KQ2M9XA", "7KQ2M9XA"),
        ("R_7kq2m9xa", "7KQ2M9XA"),
        ("ref_7KQ2M9XA", "7KQ2M9XA"),
        ("7KQ2M9XA", "7KQ2M9XA"),
        ("  r_7KQ2M9XA  ", "7KQ2M9XA"),
    ],
)
def test_normalize_code_accepts_every_shape_a_link_can_arrive_in(raw, expected) -> None:
    assert normalize_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", None, "r_", "r_SHORT", "r_7KQ2M9XAEXTRA", "r_0OIL1234", "e_507f1f77bcf86cd7"],
)
def test_normalize_code_rejects_anything_it_cannot_trust(raw) -> None:
    """A half-valid code is worse than none: it credits the wrong account."""
    assert normalize_code(raw) is None
    assert parse_ref_payload(raw) is None


def test_invite_link_opens_the_mini_app_directly() -> None:
    link = build_invite_link("7KQ2M9XA", SETTINGS)

    # startapp, not start: the invitee lands in the app, not in an empty chat.
    assert link == "https://t.me/Timemanager2026_bot/app?startapp=r_7KQ2M9XA"


# ── Reward maths ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("valid", "expected"),
    [(0, 20), (1, 20), (2, 20), (3, 40), (5, 40), (6, 60), (30, 220)],
)
def test_limit_rises_one_step_at_a_time(valid, expected) -> None:
    assert compute_event_limit(valid, SETTINGS) == expected


def test_limit_never_passes_the_hard_ceiling() -> None:
    # 24 completed steps reach 500; everything beyond has to stay there.
    assert compute_event_limit(72, SETTINGS) == 500
    assert compute_event_limit(500, SETTINGS) == 500


def test_negative_invite_counts_fall_back_to_the_base() -> None:
    assert compute_event_limit(-5, SETTINGS) == 20


@pytest.mark.parametrize(("valid", "expected"), [(0, 3), (1, 2), (2, 1), (3, 3), (4, 2)])
def test_countdown_to_the_next_bonus(valid, expected) -> None:
    assert invites_to_next_bonus(valid, SETTINGS) == expected


# ── Deep link parsing ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start r_7KQ2M9XA", "r_7KQ2M9XA"),
        ("/start@Timemanager2026_bot r_7KQ2M9XA", "r_7KQ2M9XA"),
        ("/start", ""),
        ("/start   ", ""),
        ("hello", ""),
        ("", ""),
    ],
)
def test_parse_start_payload(text, expected) -> None:
    assert parse_start_payload(text) == expected


def test_reading_the_payload_did_not_change_the_command() -> None:
    """parse_command still drops the payload — the two never share a job."""
    assert parse_command("/start r_7KQ2M9XA") == "/start"


# ── Attribution rules (fake collections, no database) ────────────────

def _matches(doc: dict, query: dict) -> bool:
    for key, condition in query.items():
        value = doc.get(key)
        if isinstance(condition, dict) and "$exists" in condition:
            if (value is not None) != condition["$exists"]:
                return False
        elif not isinstance(condition, dict) and value != condition:
            return False
    return True


class FakeUsers:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = {doc["_id"]: dict(doc) for doc in (docs or [])}

    async def find_one(self, query, projection=None, sort=None):
        if "ref_code" in query:
            for doc in self.docs.values():
                if doc.get("ref_code") == query["ref_code"]:
                    return dict(doc)
            return None
        doc = self.docs.get(query.get("_id"))
        return dict(doc) if doc and _matches(doc, query) else None

    async def update_one(self, query, update, upsert=False):
        doc = self.docs.get(query.get("_id"))
        if doc is not None and _matches(doc, query):
            doc.update(update.get("$set", {}))
            return SimpleNamespace(modified_count=1, upserted_id=None)
        if doc is None and upsert:
            new = {"_id": query["_id"]}
            new.update(update.get("$setOnInsert", {}))
            new.update(update.get("$set", {}))
            self.docs[new["_id"]] = new
            return SimpleNamespace(modified_count=0, upserted_id=new["_id"])
        return SimpleNamespace(modified_count=0, upserted_id=None)

    async def find_one_and_update(self, query, update):
        doc = self.docs.get(query.get("_id"))
        if doc is None or not _matches(doc, query):
            return None
        before = dict(doc)
        doc.update(update.get("$set", {}))
        return before

    async def count_documents(self, query, **kwargs):
        return sum(1 for doc in self.docs.values() if _matches(doc, query))


class FakeEvents:
    def __init__(self, per_user: dict[str, int] | None = None) -> None:
        self.per_user = per_user or {}

    async def count_documents(self, query, **kwargs):
        return self.per_user.get(query.get("user_id"), 0)


@pytest.fixture
def referrals(monkeypatch):
    """The service with both collections swapped for in-memory fakes."""
    from app.services import referrals as module

    users = FakeUsers(
        [
            {"_id": "100", "ref_code": "AAAA2222"},
            {"_id": "200", "ref_code": "BBBB3333"},
        ]
    )
    events = FakeEvents()

    monkeypatch.setattr(module, "get_users_collection", lambda: users)
    monkeypatch.setattr(module, "get_events_collection", lambda: events)

    return SimpleNamespace(module=module, users=users, events=events)


@pytest.mark.anyio
async def test_a_valid_invite_is_recorded_as_pending(referrals) -> None:
    assert await referrals.module.attach_referrer("300", "BBBB3333") is True

    invitee = referrals.users.docs["300"]
    assert invitee["referred_by"] == "200"
    assert invitee["referral_status"] == STATUS_PENDING


@pytest.mark.anyio
async def test_you_cannot_invite_yourself(referrals) -> None:
    assert await referrals.module.attach_referrer("100", "AAAA2222") is False
    assert "referred_by" not in referrals.users.docs["100"]


@pytest.mark.anyio
async def test_an_unknown_code_credits_nobody(referrals) -> None:
    assert await referrals.module.attach_referrer("300", "ZZZZ9999") is False


@pytest.mark.anyio
async def test_first_touch_wins_and_cannot_be_overwritten(referrals) -> None:
    await referrals.module.attach_referrer("300", "BBBB3333")

    assert await referrals.module.attach_referrer("300", "AAAA2222") is False
    assert referrals.users.docs["300"]["referred_by"] == "200"


@pytest.mark.anyio
async def test_an_established_account_cannot_be_retro_attributed(referrals) -> None:
    """Someone who already has events opening a link is not a new user."""
    referrals.events.per_user["300"] = 4

    assert await referrals.module.attach_referrer("300", "BBBB3333") is False


@pytest.mark.anyio
async def test_the_invite_turns_valid_on_the_first_event_only_once(referrals) -> None:
    await referrals.module.attach_referrer("300", "BBBB3333")

    first = await referrals.module.activate_referral_if_first_event("300")
    assert referrals.users.docs["300"]["referral_status"] == STATUS_VALID
    assert first is None  # 1 of 3 — no bonus yet, so nothing to announce

    # The user's second event must not be able to count the same invite again.
    assert await referrals.module.activate_referral_if_first_event("300") is None
    assert await referrals.module.count_valid_invites("200") == 1


@pytest.mark.anyio
async def test_the_third_valid_invite_reports_a_bonus(referrals, monkeypatch) -> None:
    monkeypatch.setattr(referrals.module, "get_settings", lambda: SETTINGS)

    for invitee in ("301", "302", "303"):
        await referrals.module.attach_referrer(invitee, "BBBB3333")

    assert await referrals.module.activate_referral_if_first_event("301") is None
    assert await referrals.module.activate_referral_if_first_event("302") is None
    assert await referrals.module.activate_referral_if_first_event("303") == ("200", 3)

    assert await referrals.module.effective_event_limit("200", SETTINGS) == 40
'''

create("tests/test_referrals.py", TESTS, "referral test suite")

patch(
    "README.md",
    old="| `MAX_EVENTS_PER_USER`, `MAX_TITLE_LEN`, `MAX_NOTE_LEN` | Per-user limits |\n",
    new=(
        "| `MAX_EVENTS_PER_USER`, `MAX_TITLE_LEN`, `MAX_NOTE_LEN` | Per-user limits "
        "(`MAX_EVENTS_PER_USER` is the hard ceiling) |\n"
        "| `EVENT_LIMIT_BASE`, `REFERRAL_STEP`, `REFERRAL_BONUS` | Referral reward: "
        "start at 20 events, +20 per 3 valid invites |\n"
    ),
    marker="EVENT_LIMIT_BASE`, `REFERRAL_STEP",
    label="document the referral settings",
)


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max(len(message) for _, message in LOG) if LOG else 0

    print("\n  TimeManager Pro — Batch 12a (referral & dynamic limit)\n")
    print("  " + "─" * min(width + 8, 76))
    for status, message in LOG:
        print(f"  [{status}] {message}")
    print("  " + "─" * min(width + 8, 76))

    if FAILED:
        print("\n  Nothing was written. Fix the files named above and run again.\n")
        return 1

    if DRY_RUN:
        print(f"\n  Dry run: {len(PENDING)} file(s) would change. Nothing written.\n")
        return 0

    flush()
    print(f"\n  {len(PENDING)} file(s) written. Next:\n")
    print("      ruff check .")
    print("      pytest")
    print("      git add -A && git commit -m 'Batch 12a: referral system and dynamic event limit'")
    print()
    print("  The three new settings have defaults, so an unchanged .env keeps working.")
    print("  The users indexes are created on the next boot by ensure_indexes().\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
