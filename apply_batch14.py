#!/usr/bin/env python3
"""
apply_batch14.py — TimeManager Pro, Batch 14 (groups and channels)

A reminder can now land in a group or a channel as well as in your own chat.
The event still belongs to you and still sits in your list; what changes is
where it speaks.

  * adding the bot to a group or channel registers it as a destination
  * other members join that destination by tapping a link, membership checked
  * a reminder goes to you AND to the chosen chat, never only to the chat
  * losing access deactivates the destination instead of failing silently

Run once from the repository root:

    python apply_batch14.py --check   # dry run, writes nothing
    python apply_batch14.py           # apply

Requires Batch 17. Safe to run twice.
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
              "— is Batch 17 applied?")
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
# 1. Where destinations live
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/db.py",
    old="""def get_users_collection() -> AsyncIOMotorCollection:
""",
    new="""def get_chats_collection() -> AsyncIOMotorCollection:
    \"\"\"Groups and channels the bot has been added to.\"\"\"
    return get_database()["chats"]


def get_users_collection() -> AsyncIOMotorCollection:
""",
    marker="def get_chats_collection",
    label="the chats collection",
)

patch(
    "app/db.py",
    old='    await events.create_index("share_id", sparse=True)\n',
    new=(
        '    await events.create_index("share_id", sparse=True)\n'
        "\n"
        "    # Batch 14. link_token is what a group member taps to add the chat\n"
        "    # to their own destinations, so it has to resolve to exactly one.\n"
        "    chats = get_chats_collection()\n"
        '    await chats.create_index("link_token", unique=True, sparse=True)\n'
        '    await chats.create_index("members")\n'
    ),
    marker='create_index("link_token"',
    label="chat indexes",
)

# ══════════════════════════════════════════════════════════════════════
# 2. The service
# ══════════════════════════════════════════════════════════════════════

SERVICE = '''from __future__ import annotations

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
'''

create("app/services/chats.py", SERVICE, "the chats service")

append(
    "app/services/telegram_api.py",
    addition='''

async def get_chat_member_status(chat_id: int, user_id: str,
                                 settings: Settings | None = None) -> str:
    """Whether this person is actually in that chat.

    Asked live rather than remembered: membership changes without telling us,
    and a stale "yes" here is somebody posting into a group they left.
    """
    settings = settings or get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/getChatMember"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                url, json={"chat_id": chat_id, "user_id": int(user_id)}
            )
        body = response.json()
    except Exception:
        logger.warning("getChatMember failed chat=%s user=%s", chat_id, user_id)
        return ""

    if not body.get("ok"):
        return ""
    return str(body["result"].get("status") or "")
''',
    marker="async def get_chat_member_status",
    label="membership lookup",
)

# ══════════════════════════════════════════════════════════════════════
# 3. The webhook learns about being added and removed
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/routes/telegram.py",
    old="""    if update.callback_query:
""",
    new="""    if update.my_chat_member:
        await _handle_membership(update, settings)
    elif update.callback_query:
""",
    marker="_handle_membership(update, settings)",
    label="route the membership update",
)

append(
    "app/routes/telegram.py",
    addition='''

async def _handle_membership(update, settings) -> None:
    """The bot was added to or removed from a group or channel.

    Telegram sends this as my_chat_member, which the webhook ignored until
    now — it only ever looked at messages. Registering the chat here is what
    makes it appear as a destination without anyone typing a command.
    """
    change = update.my_chat_member
    chat = change.chat
    status = change.new_chat_member.status

    try:
        from app.services.chats import (
            ACTIVE_STATUSES,
            ALLOWED_TYPES,
            chat_link,
            deactivate_chat,
            register_chat,
        )
        from app.utils.i18n import resolve_language, t

        if chat.type not in ALLOWED_TYPES:
            return

        if status not in ACTIVE_STATUSES:
            await deactivate_chat(chat.id)
            return

        added_by = str(change.from_user.id) if change.from_user else None
        record = await register_chat(chat.id, chat.type, chat.title or "", added_by)
        if not record or not added_by:
            return

        # Told in private, not in the group: the person who added the bot is
        # the one who needs to know what to do next, and a group does not need
        # a setup message from a bot it just met.
        language = resolve_language(
            getattr(change.from_user, "language_code", None) if change.from_user else None
        )
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=added_by,
                text=t("chat_connected", language).format(
                    title=chat.title or "",
                    link=chat_link(record["link_token"], settings),
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception:
        logger.exception("membership update failed chat_id=%s", getattr(chat, "id", "?"))
''',
    marker="async def _handle_membership",
    label="handle being added or removed",
)

patch(
    "app/utils/i18n.py",
    old="""    "share_join_button": {
""",
    new="""    "chat_connected": {
        "en": (
            "✅ <b>{title}</b> is connected.\\n\\n"
            "When you create an event you can now send its reminder there as "
            "well as here. Anyone else in that chat can add it to their own "
            "list with this link:\\n{link}"
        ),
        "fa": (
            "✅ <b>{title}</b> وصل شد.\\n\\n"
            "از این به بعد موقع ساخت رویداد می‌توانی یادآوری‌اش را علاوه بر "
            "اینجا، آنجا هم بفرستی. هر کس دیگری در آن چت با این لینک می‌تواند "
            "به فهرست خودش اضافه‌اش کند:\\n{link}"
        ),
    },
    "share_join_button": {
""",
    marker="chat_connected",
    label="copy for the connection notice",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The event carries a destination
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/schemas/requests.py",
    old="""    lead_repeat: Literal["none", "daily", "weekly", "monthly"] = "none"
""",
    new="""    lead_repeat: Literal["none", "daily", "weekly", "monthly"] = "none"
    # A group or channel the reminder also goes to. Validated against the
    # user's own destinations on save, never trusted as sent.
    target_chat_id: str | None = Field(default=None, max_length=32)
""",
    marker="target_chat_id",
    label="target_chat_id on the request",
)

patch(
    "app/schemas/responses.py",
    old="""    checklist: list[ChecklistItem] = Field(default_factory=list)
""",
    new="""    checklist: list[ChecklistItem] = Field(default_factory=list)
    target_chat_id: str | None = None
    target_chat_title: str = ""
    scope: str = "private"
""",
    marker="target_chat_title",
    label="the destination on the response",
)

# Resolved in the two async callers rather than inside _normalize_event_input:
# that function is synchronous, and turning it async to make one database
# lookup would have rippled through every call site for no gain.

DESTINATION_BLOCK = """
    # Resolved against what this user is actually allowed to post to. A chat
    # id arriving in a request means nothing on its own.
    target = await usable_destination(user_id, getattr(payload, "target_chat_id", None))
    event_data["target_chat_id"] = str(target["_id"]) if target else None
    event_data["target_chat_title"] = target.get("title", "") if target else ""
    event_data["scope"] = scope_for(target.get("type")) if target else "private"
"""

patch(
    "app/services/events.py",
    old="""    event_data = _normalize_event_input(payload, settings)
    now = datetime.now(timezone.utc)
""",
    new="""    event_data = _normalize_event_input(payload, settings)
""" + DESTINATION_BLOCK + """    now = datetime.now(timezone.utc)
""",
    marker='event_data["target_chat_id"] = str(target["_id"]) if target else None\n    event_data["target_chat_title"] = target.get("title", "") if target else ""\n    event_data["scope"] = scope_for(target.get("type")) if target else "private"\n    now = datetime',
    label="resolve the destination when adding",
)

patch(
    "app/services/events.py",
    old="""    event_data = _normalize_event_input(payload, settings)
    event_data["updated_at"] = datetime.now(timezone.utc)
""",
    new="""    event_data = _normalize_event_input(payload, settings)
""" + DESTINATION_BLOCK + """    event_data["updated_at"] = datetime.now(timezone.utc)
""",
    marker='if target else "private"\n    event_data["updated_at"]',
    label="resolve the destination when editing",
)

patch(
    "app/services/events.py",
    old="""        checklist=[
""",
    new="""        target_chat_id=doc.get("target_chat_id"),
        target_chat_title=doc.get("target_chat_title", ""),
        scope=doc.get("scope", "private"),
        checklist=[
""",
    marker='target_chat_id=doc.get("target_chat_id")',
    label="return the destination",
)

patch(
    "app/services/events.py",
    old="""from app.utils.dates import (
    build_lead_reminders,
""",
    new="""from app.services.chats import scope_for, usable_destination
from app.utils.dates import (
    build_lead_reminders,
""",
    marker="from app.services.chats import",
    label="import the chat helpers",
)

# ══════════════════════════════════════════════════════════════════════
# 5. The worker delivers to both
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/reminders.py",
    old="""            await bot.send_message(
                chat_id=evt["user_id"],
                text=build_reminder_text(evt),
                parse_mode="HTML",
                reply_markup=build_reminder_keyboard(evt, settings),
            )
""",
    new="""            await bot.send_message(
                chat_id=evt["user_id"],
                text=build_reminder_text(evt),
                parse_mode="HTML",
                reply_markup=build_reminder_keyboard(evt, settings),
            )

            # The group copy comes second and in its own try: the personal
            # reminder has already been delivered, and a bot that was removed
            # from a group must not cost the owner their own reminder or stop
            # the series being rescheduled below.
            target = evt.get("target_chat_id")
            if target:
                try:
                    await bot.send_message(
                        chat_id=int(target),
                        text=build_reminder_text(evt),
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.warning(
                        "group reminder failed chat=%s event=%s", target, evt["_id"]
                    )
                    try:
                        from app.services.chats import deactivate_chat

                        await deactivate_chat(int(target))
                    except Exception:
                        logger.exception("could not deactivate chat %s", target)
""",
    marker="The group copy comes second",
    label="send the reminder to the chat as well",
)

# ══════════════════════════════════════════════════════════════════════
# 6. The API for the picker
# ══════════════════════════════════════════════════════════════════════

ROUTE = '''from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import Field

from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.chats import destinations_for, link_member, parse_chat_payload

router = APIRouter(tags=["chats"])
logger = logging.getLogger("tm_pro.chats_api")


class LinkPayload(InitDataPayload):
    token: str = Field(min_length=1, max_length=80)


@router.post("/api/chats")
async def api_chats(request: Request, payload: InitDataPayload) -> dict:
    """The destinations this user may send to, for the picker in the form."""
    auth = await validate_init_data(request, payload.initData, scope=READ_SCOPE)
    return {"success": True, "chats": await destinations_for(auth["user_id"])}


@router.post("/api/chats/link")
async def api_chats_link(request: Request, payload: LinkPayload) -> dict:
    """Someone tapped the link the bot posted. Membership decides, not the link."""
    auth = await validate_init_data(request, payload.initData, scope=WRITE_SCOPE)

    token = parse_chat_payload(payload.token) or payload.token
    return await link_member(auth["user_id"], token)
'''

create("app/routes/chats.py", ROUTE, "the chats endpoints")

patch(
    "app/main.py",
    old="from app.routes.calendar import router as calendar_router\n",
    new=(
        "from app.routes.calendar import router as calendar_router\n"
        "from app.routes.chats import router as chats_router\n"
    ),
    marker="chats_router",
    label="import the chats router",
)

patch(
    "app/main.py",
    old="app.include_router(calendar_router)\n",
    new=(
        "app.include_router(calendar_router)\n"
        "app.include_router(chats_router)\n"
    ),
    marker="include_router(chats_router)",
    label="register the chats router",
)

# ══════════════════════════════════════════════════════════════════════
# 7. The picker in the form
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""          <p class="field-hint" data-i18n>Reminders before the event. They stop once the day arrives.</p>
        </div>
""",
    new="""          <p class="field-hint" data-i18n>Reminders before the event. They stop once the day arrives.</p>
        </div>
        <div class="field-group" id="targetChatWrap" hidden>
          <label class="field-label" for="targetChat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
            <span data-i18n>Also send it to</span>
          </label>
          <select id="targetChat" class="field-input field-select">
            <option value="">Only me</option>
          </select>
          <p class="field-hint" data-i18n>Everyone in that chat will see the event title.</p>
        </div>
""",
    marker='id="targetChatWrap"',
    label="the destination picker",
)

patch(
    "static/app.js",
    old="""      lead_repeat: document.getElementById("leadRepeat")?.value || "none",
""",
    new="""      lead_repeat: document.getElementById("leadRepeat")?.value || "none",
      target_chat_id: document.getElementById("targetChat")?.value || null,
""",
    marker="target_chat_id: document.getElementById",
    label="send the destination",
)

DESTINATIONS_JS = r'''  /* ── Reminder destinations ───────────────────────────
     Groups and channels the bot has been added to, filtered server-side to
     the ones this person is actually in. The picker stays hidden until there
     is at least one, so nobody meets an empty dropdown asking a question they
     have no answer to. */

  let destinations = [];

  async function loadDestinations() {
    try {
      const data = await apiPost("/api/chats", {});
      destinations = Array.isArray(data.chats) ? data.chats : [];
    } catch (_) {
      destinations = [];
    }

    const wrap = document.getElementById("targetChatWrap");
    const select = document.getElementById("targetChat");
    if (!wrap || !select) return;

    const keep = select.value;
    select.replaceChildren();

    const none = document.createElement("option");
    none.value = "";
    none.textContent = t("Only me");
    select.appendChild(none);

    destinations.forEach((chat) => {
      const option = document.createElement("option");
      option.value = chat.id;
      option.textContent = chat.title || chat.id;
      select.appendChild(option);
    });

    select.value = keep;
    wrap.hidden = destinations.length === 0;
  }

  function setDestination(value) {
    const select = document.getElementById("targetChat");
    if (select) select.value = value || "";
  }

'''

patch(
    "static/app.js",
    old="""  /* ── Checklist ───────────────────────────────────────
""",
    new=DESTINATIONS_JS + """  /* ── Checklist ───────────────────────────────────────
""",
    marker="/* ── Reminder destinations ─",
    label="load the destinations",
)

patch(
    "static/app.js",
    old="""    if (leadSelect) leadSelect.value = event.lead_repeat || "none";
""",
    new="""    if (leadSelect) leadSelect.value = event.lead_repeat || "none";
    setDestination(event.target_chat_id);
""",
    marker="setDestination(event.target_chat_id)",
    label="round-trip the destination",
)

patch(
    "static/app.js",
    old="""  bindChecklist();
""",
    new="""  bindChecklist();
  loadDestinations();
""",
    marker="loadDestinations();\n",
    label="fetch them on boot",
)

patch(
    "static/app.js",
    old="""      "Checklist": "چک‌لیست",
""",
    new="""      "Checklist": "چک‌لیست",
      "Also send it to": "علاوه بر این، بفرست به",
      "Only me": "فقط خودم",
      "Everyone in that chat will see the event title.": "همهٔ اعضای آن چت عنوان رویداد را می‌بینند.",
""",
    marker="علاوه بر این، بفرست به",
    label="Persian for the picker",
)

# ══════════════════════════════════════════════════════════════════════
# 8. Joining a chat from the link the bot posted
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/invite.js",
    old="""    var param = String((tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "");
    if (param.toLowerCase().indexOf("s_") !== 0) return;
""",
    new="""    var param = String((tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "");

    // The fourth kind of deep link in this app, and the only one that does its
    // work without asking: linking a chat you are already in adds nothing to
    // your calendar, it only tells the app where you are allowed to send.
    if (param.toLowerCase().indexOf("g_") === 0) {
      try {
        var linked = await api("/api/chats/link", { token: param.slice(2) });
        if (linked.success && window.TMApp && window.TMApp.reload) {
          window.TMApp.reload();
        }
      } catch (_) {}
      return;
    }

    if (param.toLowerCase().indexOf("s_") !== 0) return;
""",
    marker="The fourth kind of deep link",
    label="link a chat from its token",
)

# ══════════════════════════════════════════════════════════════════════
# 9. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chats import (
    ALLOWED_TYPES,
    chat_link,
    generate_link_token,
    parse_chat_payload,
    scope_for,
)

SETTINGS = SimpleNamespace(
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


def test_tokens_are_unique_and_url_safe() -> None:
    tokens = {generate_link_token() for _ in range(200)}

    assert len(tokens) == 200
    assert all(all(c.isalnum() or c in "-_" for c in token) for token in tokens)


def test_the_chat_link_opens_the_mini_app() -> None:
    assert chat_link("abcdefghijkl", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=g_abcdefghijkl"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("g_abcdefghijkl", "abcdefghijkl"),
        ("G_abcdefghijkl", "abcdefghijkl"),
        ("s_abcdefghijkl", None),        # a shared event
        ("r_7KQ2M9XA", None),            # a referral
        ("e_abc123DEF456ghi789", None),  # a public countdown
        ("g_short", None),
        ("", None),
        (None, None),
    ],
)
def test_four_kinds_of_deep_link_stay_out_of_each_other(payload, expected) -> None:
    assert parse_chat_payload(payload) == expected


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [("group", "group"), ("supergroup", "group"), ("channel", "channel"),
     ("private", "private"), (None, "private")],
)
def test_scope_follows_the_chat_type(chat_type, expected) -> None:
    assert scope_for(chat_type) == expected


def test_a_private_chat_is_never_a_destination() -> None:
    """It is where reminders already go; offering it would send two copies."""
    assert "private" not in ALLOWED_TYPES
    assert {"group", "supergroup", "channel"} == ALLOWED_TYPES
'''

create("tests/test_chats.py", TESTS, "chat destination tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 14 (groups and channels)\n")
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
    print("      git add -A && git commit -m 'Batch 14: reminders in groups and channels'")
    print()
    print("  Add the bot to a group: it should message you privately with a link.")
    print("  Then the picker appears in the event form.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
