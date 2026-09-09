#!/usr/bin/env python3
"""
apply_batch12c.py — TimeManager Pro, Batch 12c (shared events)

One event, linked across two or more people. Each of them keeps their own
copy, so each keeps their own timezone, reminder time, note and pin, while
the title, date, time, repeat and category stay in step.

  * only the creator can change the shared fields
  * if the creator deletes the event, every copy goes with it
  * a joined copy counts against the joiner's event limit
  * the creator's name is shown when the invite is opened and never stored

Run once from the repository root:

    python apply_batch12c.py --check   # dry run, writes nothing
    python apply_batch12c.py           # apply

Requires Batch 15. Safe to run twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRY_RUN = "--check" in sys.argv

PENDING: dict[Path, str] = {}
LOG: list[tuple[str, str]] = []
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
    return path.read_text(encoding="utf-8") if path.exists() else None


def patch(rel: str, old: str, new: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found — are you in the repository root?")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    count = text.count(old)
    if count != 1:
        _fail(f"{rel}: anchor for '{label}' matched {count} times, expected 1 "
              "— is Batch 15 applied?")
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
# 1. Indexes
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/db.py",
    old='    await events.create_index("public_token", unique=True, sparse=True)\n',
    new=(
        '    await events.create_index("public_token", unique=True, sparse=True)\n'
        "\n"
        "    # Batch 12c. The token is unique because it is the only thing that\n"
        "    # decides which event a stranger is about to join; share_id is the\n"
        "    # key every propagation and cascade walks.\n"
        '    await events.create_index("share_token", unique=True, sparse=True)\n'
        '    await events.create_index("share_id", sparse=True)\n'
    ),
    marker='create_index("share_token"',
    label="share indexes",
)

# ══════════════════════════════════════════════════════════════════════
# 2. The service
# ══════════════════════════════════════════════════════════════════════

SERVICE = '''from __future__ import annotations

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

    return {
        "success": True,
        "token": token,
        "invite_url": invite_url(token, settings),
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
'''

create("app/services/share_group.py", SERVICE, "shared event service")

# ══════════════════════════════════════════════════════════════════════
# 3. Hooks in the event paths
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/events.py",
    old="""    await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": event_data},
    )
""",
    new="""    # A member may keep their own note, reminder and pin, but the shared
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
""",
    marker="share propagation failed",
    label="propagate an owner's edit",
)

patch(
    "app/services/events.py",
    old="""    result = await events_coll.delete_one({"_id": oid, "user_id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")
""",
    new="""    # Deleted and returned in one step, because the document is what says
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
""",
    marker="share cascade failed",
    label="cascade the creator's delete",
)

patch(
    "app/services/events.py",
    old="""        lead_repeat=doc.get("lead_repeat", "none"),
    )
""",
    new="""        lead_repeat=doc.get("lead_repeat", "none"),
        share_role=doc.get("share_role"),
    )
""",
    marker="share_role=doc.get",
    label="expose the share role",
)

patch(
    "app/schemas/responses.py",
    old="""    lead_repeat: str = "none"
""",
    new="""    lead_repeat: str = "none"
    # None for an ordinary event, "owner" or "member" for a shared one.
    share_role: str | None = None
""",
    marker="share_role",
    label="share_role on the response",
)

# ══════════════════════════════════════════════════════════════════════
# 4. Looking up a name without keeping it
# ══════════════════════════════════════════════════════════════════════

append(
    "app/services/telegram_api.py",
    addition='''

async def get_first_name(user_id: str, settings: Settings | None = None) -> str:
    """The inviter's first name, asked for at the moment it is shown.

    Deliberately not stored anywhere. An invite has to say who it is from or
    nobody will tap it, but that is a reason to display a name for one screen,
    not a reason to keep a copy of it on somebody else's account.
    """
    settings = settings or get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/getChat"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, json={"chat_id": int(user_id)})
        body = response.json()
    except Exception:
        logger.warning("getChat failed for %s", user_id)
        return ""

    if not body.get("ok"):
        return ""
    return str(body["result"].get("first_name") or "")
''',
    marker="async def get_first_name",
    label="live name lookup",
)

# ══════════════════════════════════════════════════════════════════════
# 5. Routes
# ══════════════════════════════════════════════════════════════════════

ROUTE = '''from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.share_group import (
    find_by_token,
    invite_url,
    join_group,
    member_count,
    parse_share_payload,
    start_group,
)
from app.services.telegram_api import get_first_name
from app.utils.ids import safe_object_id

router = APIRouter(tags=["shared-events"])
logger = logging.getLogger("tm_pro.sharegroup")


class GroupPayload(InitDataPayload):
    event_id: str = Field(min_length=1, max_length=64)


class TokenPayload(InitDataPayload):
    token: str = Field(min_length=1, max_length=80)


class JoinPayload(TokenPayload):
    timezone: str = Field(default="UTC", min_length=1, max_length=128)


@router.post("/api/group/link")
async def api_group_link(request: Request, payload: GroupPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    return await start_group(auth["user_id"], oid, settings)


@router.post("/api/group/invite")
async def api_group_invite(request: Request, payload: TokenPayload) -> dict:
    """What the invited person sees before deciding.

    The name is fetched live and returned for this one screen; nothing about
    the inviter is written to the joiner's account.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    token = parse_share_payload(payload.token) or payload.token
    origin = await find_by_token(token)
    if not origin:
        raise HTTPException(status_code=404, detail="INVITE_NOT_FOUND")

    owner_id = str(origin.get("user_id"))
    return {
        "success": True,
        "title": origin.get("title", ""),
        "date_iso": origin.get("date_iso", ""),
        "date_jalali": origin.get("date_jalali", ""),
        "category": origin.get("category", "general"),
        "from_name": await get_first_name(owner_id, settings),
        "is_own": owner_id == str(auth["user_id"]),
        "members": await member_count(origin.get("share_id")),
    }


@router.post("/api/group/join")
async def api_group_join(request: Request, payload: JoinPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    token = parse_share_payload(payload.token) or payload.token
    return await join_group(auth["user_id"], token, payload.timezone, settings)


@router.post("/api/group/state")
async def api_group_state(request: Request, payload: GroupPayload) -> dict:
    """Only for the invite button: does this event already have a link?"""
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    from app.db import get_events_collection

    event = await get_events_collection().find_one(
        {"_id": oid, "user_id": auth["user_id"]},
        {"share_id": 1, "share_role": 1, "share_token": 1},
    )
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    token = event.get("share_token")
    return {
        "success": True,
        "role": event.get("share_role"),
        "invite_url": invite_url(token, settings) if token else None,
        "members": await member_count(event.get("share_id")),
    }
'''

create("app/routes/sharegroup.py", ROUTE, "shared event endpoints")

patch(
    "app/main.py",
    old="from app.routes.share import router as share_router\n",
    new=(
        "from app.routes.share import router as share_router\n"
        "from app.routes.sharegroup import router as sharegroup_router\n"
    ),
    marker="sharegroup_router",
    label="import the group router",
)

patch(
    "app/main.py",
    old="app.include_router(share_router)\n",
    new=(
        "app.include_router(share_router)\n"
        "app.include_router(sharegroup_router)\n"
    ),
    marker="include_router(sharegroup_router)",
    label="register the group router",
)

# ══════════════════════════════════════════════════════════════════════
# 6. The invite screen
# ══════════════════════════════════════════════════════════════════════

INVITE_JS = r'''/* ──────────────────────────────────────────────────────────────
   invite.js — Batch 12c: joining a shared event.

   Runs once on boot. If the app was opened from a ?startapp=s_<token> link
   it asks what the invite is, shows it, and adds the event only if the
   person says yes. Everything else in the app is untouched.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "Shared event": "رویداد مشترک",
    "{name} wants to share this event with you": "{name} می‌خواهد این رویداد را با شما به اشتراک بگذارد",
    "Someone wants to share this event with you": "کسی می‌خواهد این رویداد را با شما به اشتراک بگذارد",
    "Add to my events": "به رویدادهای من اضافه کن",
    "Not now": "الان نه",
    "Added. It stays in step with the original.": "اضافه شد. با نسخهٔ اصلی همگام می‌ماند.",
    "You already have this one.": "این را از قبل داری.",
    "This is your own event.": "این رویداد خودت است.",
    "Your event limit is full.": "سقف رویدادهایت پر است.",
    "Could not add it.": "اضافه نشد.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text, vars) {
    var out = isFa && FA[text] ? FA[text] : text;
    if (vars) {
      Object.keys(vars).forEach(function (k) { out = out.split("{" + k + "}").join(vars[k]); });
    }
    return out;
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  async function api(path, body) {
    var response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ initData: (tg && tg.initData) || "" }, body)),
    });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(String(data.detail || response.status));
    return data;
  }

  function close(overlay) {
    overlay.classList.remove("is-open");
    setTimeout(function () { overlay.remove(); }, 200);
  }

  function show(invite, token) {
    var overlay = document.createElement("div");
    overlay.className = "invite-overlay";
    if (isFa) overlay.setAttribute("dir", "rtl");

    var who = invite.from_name
      ? t("{name} wants to share this event with you", { name: invite.from_name })
      : t("Someone wants to share this event with you");

    overlay.innerHTML =
      '<div class="invite-card" role="dialog" aria-modal="true">' +
        '<span class="invite-tag">' + t("Shared event") + "</span>" +
        '<p class="invite-who"></p>' +
        '<h3 class="invite-title"></h3>' +
        '<p class="invite-date"></p>' +
        '<p class="invite-error" id="inviteError" hidden></p>' +
        '<div class="invite-actions">' +
          '<button type="button" class="btn-secondary" id="inviteNo"></button>' +
          '<button type="button" class="btn-primary" id="inviteYes"></button>' +
        "</div>" +
      "</div>";

    document.body.appendChild(overlay);
    overlay.querySelector(".invite-who").textContent = who;
    overlay.querySelector(".invite-title").textContent = invite.title || "";
    overlay.querySelector(".invite-date").textContent =
      isFa ? (invite.date_jalali || invite.date_iso || "") : (invite.date_iso || "");
    overlay.querySelector("#inviteNo").textContent = t("Not now");
    overlay.querySelector("#inviteYes").textContent = t("Add to my events");

    requestAnimationFrame(function () { overlay.classList.add("is-open"); });

    overlay.querySelector("#inviteNo").addEventListener("click", function () {
      close(overlay);
    });

    overlay.querySelector("#inviteYes").addEventListener("click", async function () {
      var yes = overlay.querySelector("#inviteYes");
      var problem = overlay.querySelector("#inviteError");
      yes.disabled = true;

      try {
        var result = await api("/api/group/join", {
          token: token,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        });
        haptic("success");
        close(overlay);
        if (window.TMApp && window.TMApp.reload) window.TMApp.reload();
        if (result.already_joined) alert(t("You already have this one."));
      } catch (error) {
        var code = String(error.message || "");
        problem.textContent =
          code.indexOf("EVENT_LIMIT_REACHED") >= 0 ? t("Your event limit is full.")
          : code.indexOf("ALREADY_YOURS") >= 0 ? t("This is your own event.")
          : t("Could not add it.");
        problem.hidden = false;
        yes.disabled = false;
      }
    });
  }

  async function init() {
    var param = String((tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "");
    if (param.toLowerCase().indexOf("s_") !== 0) return;

    var token = param.slice(2);
    try {
      var invite = await api("/api/group/invite", { token: token });
      // Opening your own invite link is not an error, it just has nothing to
      // offer — the event is already there.
      if (!invite.is_own) show(invite, token);
    } catch (_) {
      /* A dead or malformed link simply opens the app as normal. */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
'''

create("static/invite.js", INVITE_JS, "the join screen")

patch(
    "templates/index.html",
    old='  <script src="/static/views.js?v={{ asset_version }}" defer></script>\n',
    new=(
        '  <script src="/static/views.js?v={{ asset_version }}" defer></script>\n'
        '  <script src="/static/invite.js?v={{ asset_version }}" defer></script>\n'
    ),
    marker="invite.js",
    label="load invite.js",
)

patch(
    "app/routes/web.py",
    old='    for name in ("style.css", "app.js", "referral.js", "share.js", "views.js"):\n',
    new='    for name in ("style.css", "app.js", "referral.js", "share.js", "views.js",\n'
        '                 "invite.js"):\n',
    marker='"invite.js"',
    label="cache-bust invite.js",
)

# ══════════════════════════════════════════════════════════════════════
# 7. The invite button, inside the share sheet
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/share.js",
    old="""        '<div class="shr-actions">' +
          '<button type="button" class="btn-secondary" id="shrCopy">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="shrSend">' + t("Send in Telegram") + "</button>" +
        "</div>" +
""",
    new="""        '<div class="shr-actions">' +
          '<button type="button" class="btn-secondary" id="shrCopy">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="shrSend">' + t("Send in Telegram") + "</button>" +
        "</div>" +
        '<button type="button" class="btn-secondary shr-group" id="shrGroup">' +
          t("Share the event itself with someone") + "</button>" +
        '<p class="shr-hint" id="shrGroupHint"></p>' +
""",
    marker='id="shrGroup"',
    label="invite button in the share sheet",
)

patch(
    "static/share.js",
    old="""    els.copy.addEventListener("click", copyLink);
    els.send.addEventListener("click", sendCard);
""",
    new="""    els.copy.addEventListener("click", copyLink);
    els.send.addEventListener("click", sendCard);

    els.group = overlay.querySelector("#shrGroup");
    els.groupHint = overlay.querySelector("#shrGroupHint");
    els.group.addEventListener("click", inviteToEvent);
""",
    marker="inviteToEvent",
    label="wire the invite button",
)

patch(
    "static/share.js",
    old="""  window.TMShare = { open: open };
""",
    new="""  // Sharing the picture and sharing the event are different things: one sends
  // a snapshot, the other links two calendars together. Same sheet, separate
  // buttons, so nobody links an account when they meant to post an image.
  async function inviteToEvent() {
    if (!current) return;
    haptic("success");
    els.group.disabled = true;

    try {
      var group = await api("/api/group/link", { event_id: current });
      var url = "https://t.me/share/url?url=" + encodeURIComponent(group.invite_url);
      if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
      else window.open(url, "_blank");
      els.groupHint.textContent = t("Anyone who opens this link gets their own copy, kept in step with yours.");
    } catch (_) {
      els.groupHint.textContent = t("Something went wrong.");
    } finally {
      els.group.disabled = false;
    }
  }

  window.TMShare = { open: open };
""",
    marker="async function inviteToEvent",
    label="ask the server for an invite link",
)

patch(
    "static/share.js",
    old="""    "Something went wrong.": "مشکلی پیش آمد.",
""",
    new="""    "Something went wrong.": "مشکلی پیش آمد.",
    "Share the event itself with someone": "این رویداد را با کسی مشترک کن",
    "Anyone who opens this link gets their own copy, kept in step with yours.":
      "هر کسی این لینک را باز کند نسخهٔ خودش را می‌گیرد که با نسخهٔ تو همگام می‌ماند.",
""",
    marker="این رویداد را با کسی مشترک کن",
    label="Persian for the invite button",
)

# ══════════════════════════════════════════════════════════════════════
# 8. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 12c — shared events ───────────────────────── */

.shr-group { width: 100%; margin-top: 10px; }

.invite-overlay {
  position: fixed; inset: 0; z-index: 85;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: rgba(10, 12, 30, 0.6);
  opacity: 0; transition: opacity 200ms ease;
}
.invite-overlay.is-open { opacity: 1; }

.invite-card {
  width: min(100%, 400px);
  padding: 24px;
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-card);
  text-align: center;
}
.invite-tag {
  display: inline-block; padding: 4px 14px; margin-bottom: 14px;
  border-radius: var(--r-pill);
  background: rgba(91, 108, 248, 0.12); color: var(--brand);
  font-size: 0.72rem; font-weight: 800;
}
.invite-who { margin: 0; font-size: 0.85rem; color: var(--text-muted); }
.invite-title { margin: 8px 0 4px; font-size: 1.25rem; font-weight: 800; }
.invite-date { margin: 0; font-size: 0.85rem; color: var(--text-2); }
.invite-error {
  margin: 14px 0 0; font-size: 0.82rem; color: var(--danger);
}
.invite-actions {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 20px;
}

/* A member cannot change what the event is or when it happens, so the fields
   that carry those say so instead of failing quietly on save. */
.is-shared-member .field-input:disabled,
.is-shared-member .field-select:disabled { opacity: 0.65; }

@media (prefers-reduced-motion: reduce) {
  .invite-overlay { transition: none; }
}
'''

append("static/style.css", STYLES, "Batch 12c — shared events", "shared event styles")

# ══════════════════════════════════════════════════════════════════════
# 9. A member's editor is read-only where it matters
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    const leadSelect = document.getElementById("leadRepeat");
    if (leadSelect) leadSelect.value = event.lead_repeat || "none";
""",
    new="""    const leadSelect = document.getElementById("leadRepeat");
    if (leadSelect) leadSelect.value = event.lead_repeat || "none";

    // On a shared copy the creator owns what the event is and when it is. The
    // server drops those fields on save anyway; disabling them here is what
    // stops someone typing a new title and wondering where it went.
    const isMember = event.share_role === "member";
    document.body.classList.toggle("is-shared-member", isMember);
    ["title", "date", "dateJalali", "repeat", "repeatUntil", "category",
     "allDay", "eventTime"].forEach((key) => {
      if (els[key]) els[key].disabled = isMember;
    });
""",
    marker="is-shared-member",
    label="lock the shared fields for a member",
)

patch(
    "static/app.js",
    old="""    f.pin.hidden = !event.pinned;
""",
    new="""    f.pin.hidden = !event.pinned;
    art.classList.toggle("is-shared", !!event.share_role);
""",
    marker='art.classList.toggle("is-shared"',
    label="mark shared events in the list",
)

# ══════════════════════════════════════════════════════════════════════
# 10. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.share_group import (
    SHARED_FIELDS,
    generate_share_token,
    invite_url,
    parse_share_payload,
)

SETTINGS = SimpleNamespace(
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


def test_tokens_are_long_and_unique() -> None:
    tokens = {generate_share_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 12
        assert all(c.isalnum() or c in "-_" for c in token)


def test_the_invite_link_opens_the_mini_app() -> None:
    assert invite_url("abcdefghijkl", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=s_abcdefghijkl"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("s_abcdefghijkl", "abcdefghijkl"),
        ("S_abcdefghijkl", "abcdefghijkl"),
        ("r_7KQ2M9XA", None),          # a referral payload, not ours
        ("e_abc123DEF456ghi789", None),  # a public countdown payload
        ("s_short", None),
        ("s_bad token", None),
        ("", None),
        (None, None),
    ],
)
def test_only_our_own_share_payloads_resolve(payload, expected) -> None:
    """Three kinds of deep link now share one entry point, and each has to
    keep its hands off the other two."""
    assert parse_share_payload(payload) == expected


def test_the_shared_set_is_what_the_event_is_and_when() -> None:
    """The split is the whole design: everything personal stays out of it."""
    assert set(SHARED_FIELDS) == {
        "title", "date_iso", "date_jalali", "all_day", "time_hm",
        "repeat", "repeat_until", "category",
    }
    for personal in ("note", "pinned", "tz_name", "reminders",
                     "reminder_hour", "lead_repeat", "user_id"):
        assert personal not in SHARED_FIELDS
'''

create("tests/test_share_group.py", TESTS, "shared event tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 12c (shared events)\n")
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
    print("      ruff check . && pytest")
    print("      git add -A && git commit -m 'Batch 12c: shared events'")
    print()
    print("  Restart the web app so ensure_indexes() creates the share indexes.")
    print("  Testing this one needs two Telegram accounts.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
