#!/usr/bin/env python3
"""
apply_batch12b.py — TimeManager Pro, Batch 12b (share card, public countdown link)

Run once from the repository root:

    python apply_batch12b.py --check   # dry run, writes nothing
    python apply_batch12b.py           # apply

Safe to run twice: every step detects its own marker and skips. Nothing is
written unless all steps resolve, so a failed anchor leaves the tree untouched.

Requires Batch 12a to be applied first.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRY_RUN = "--check" in sys.argv

PENDING: dict[Path, str] = {}
BINARY: dict[Path, bytes] = {}
LOG: list[tuple[str, str]] = []
FAILED = False
NOTES: list[str] = []


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
        _fail(f"{rel}: anchor for '{label}' not found — is Batch 12a applied?")
        return
    if count > 1:
        _fail(f"{rel}: anchor for '{label}' matches {count} times — too ambiguous")
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


def fetch_font(rel: str, url: str, label: str) -> None:
    """Fonts are the one thing this script cannot carry inside itself.

    A .ttf is binary, so it is downloaded at apply time. A failure here is not
    fatal — the rest of the patch is still valid, the card renderer just needs
    the file before it can draw, and the summary says exactly where to put it.
    """
    path = ROOT / rel
    if path.exists():
        _note("SKIP", f"{rel}: {label} (already present)")
        return

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except Exception as exc:
        NOTES.append(f"Font not downloaded ({exc}). Fetch it manually: {url} -> {rel}")
        _note("WARN", f"{rel}: {label} — download failed, see the note below")
        return

    if len(data) < 20_000 or data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        NOTES.append(f"Download for {rel} was not a font file. Fetch it manually: {url}")
        _note("WARN", f"{rel}: {label} — unexpected content, see the note below")
        return

    BINARY[path] = data
    _note(" OK ", f"{rel}: {label} ({len(data) // 1024} KB)")


def flush() -> None:
    for path, content in PENDING.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path, data in BINARY.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


# ══════════════════════════════════════════════════════════════════════
# 1. Dependencies
# ══════════════════════════════════════════════════════════════════════

patch(
    "requirements.txt",
    old="jdatetime==5.0.0\n",
    new=(
        "jdatetime==5.0.0\n"
        "\n"
        "# ==================== SHARE CARDS (Batch 12b) ====================\n"
        "Pillow==11.3.0\n"
        "# Only used when Pillow was built without Raqm; the wheels bundle it,\n"
        "# so on a normal install these two never run.\n"
        "arabic-reshaper==3.0.1\n"
        "python-bidi==0.6.11\n"
    ),
    marker="Pillow==",
    label="Pillow and the RTL fallback",
)

FONT_BASE = "https://raw.githubusercontent.com/rastikerdar/vazirmatn/v33.003/fonts/ttf"
for weight in ("Medium", "Bold", "Black"):
    fetch_font(
        f"static/fonts/Vazirmatn-{weight}.ttf",
        f"{FONT_BASE}/Vazirmatn-{weight}.ttf",
        f"Vazirmatn {weight}",
    )

create(
    "static/fonts/README.md",
    """# Fonts

Vazirmatn v33.003, by Saber Rastikerdar — https://github.com/rastikerdar/vazirmatn
Licensed under the SIL Open Font License 1.1.

Three weights are committed because the share card in `app/services/cards.py`
draws with them at render time. They cover Persian and Latin from one family,
so a card never falls back to a second typeface halfway through a line.

Emoji are deliberately absent: Pillow cannot draw colour emoji, so the card
uses worded labels instead of pictograms.
""",
    "font licence note",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Index for the public token
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/db.py",
    old='    await events.create_index([("notify_status", 1), ("processing_started_at", 1)])\n',
    new=(
        '    await events.create_index([("notify_status", 1), ("processing_started_at", 1)])\n'
        "\n"
        "    # Batch 12b. Sparse because only events the owner has actually shared\n"
        "    # carry a token, and unique because the token is the only thing\n"
        "    # standing between a public URL and someone else's event.\n"
        '    await events.create_index("public_token", unique=True, sparse=True)\n'
    ),
    marker='create_index("public_token"',
    label="public token index",
)

# ══════════════════════════════════════════════════════════════════════
# 3. The card renderer
# ══════════════════════════════════════════════════════════════════════

CARDS_SERVICE = '''from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from app.utils.i18n import category_label, t

logger = logging.getLogger("tm_pro.cards")

FONT_DIR = Path(__file__).resolve().parents[2] / "static" / "fonts"
SIZE = 1080

# Deeper than the gradient the Mini App uses on screen. On screen there is a
# blurred backdrop behind the white text; on a flat PNG there is not, and the
# lighter brand tones lose their contrast against it.
BRAND_CORNERS = ((67, 78, 224), (88, 72, 232), (96, 68, 234), (124, 58, 237))
PILL_TEXT = (79, 70, 229, 255)

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# Pillow's wheels bundle Raqm, which performs Arabic joining and the bidi
# algorithm itself. Reshaping before handing text over would apply both a
# second time and render every Persian word backwards, so the manual path
# below exists only for a Pillow built from source without Raqm.
HAS_RAQM = features.check("raqm")


class FontsMissing(RuntimeError):
    """Raised when static/fonts has not been populated."""


def fonts_available() -> bool:
    return all((FONT_DIR / f"Vazirmatn-{w}.ttf").exists() for w in ("Medium", "Bold", "Black"))


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"Vazirmatn-{weight}.ttf"
    if not path.exists():
        raise FontsMissing(f"missing {path}")
    return ImageFont.truetype(str(path), size)


def _shape(text: str, rtl: bool) -> str:
    if not rtl or HAS_RAQM:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        logger.warning("no RTL shaping available; Persian text will render disjointed")
        return text


def _kwargs(rtl: bool) -> dict:
    return {"direction": "rtl", "language": "fa"} if (rtl and HAS_RAQM) else {}


def _measure(font: ImageFont.FreeTypeFont, text: str, rtl: bool) -> float:
    return font.getlength(text, **_kwargs(rtl))


def _digits(value, rtl: bool) -> str:
    text = str(value)
    return text.translate(PERSIAN_DIGITS) if rtl else text


def _zone(name: str | None):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return timezone.utc


def days_until(event: dict, now: datetime | None = None) -> int:
    """Whole days between today and the event, in the event's own timezone.

    event_ts_utc wins over date_iso because for a recurring event it already
    holds the next occurrence, and that is the number a countdown should show.
    Comparing calendar dates rather than instants is deliberate: "tomorrow"
    has to stay 1 whether the event is at 00:30 or at 23:30.
    """
    zone = _zone(event.get("tz_name"))
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()

    stamp = event.get("event_ts_utc")
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        target = stamp.astimezone(zone).date()
    else:
        try:
            target = date.fromisoformat(str(event.get("date_iso", "")))
        except ValueError:
            return 0

    return (target - today).days


def _gradient() -> Image.Image:
    """Four corner colours blown up to full size — cheap and perfectly smooth."""
    small = Image.new("RGB", (2, 2))
    for index, colour in enumerate(BRAND_CORNERS):
        small.putpixel((index % 2, index // 2), colour)
    return small.resize((SIZE, SIZE), Image.Resampling.BICUBIC)


def _glow(base: Image.Image, cx: int, cy: int, radius: int, alpha: int) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 255, 255, alpha)
    )
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(40)))


def _fit(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int,
         rtl: bool) -> list[str]:
    lines: list[str] = []
    current = ""

    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _measure(font, candidate, rtl) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if lines and _measure(font, lines[-1], rtl) > width:
        trimmed = lines[-1]
        while trimmed and _measure(font, trimmed + "…", rtl) > width:
            trimmed = trimmed[:-1]
        lines[-1] = trimmed + "…"

    return lines or [""]


def _headline(event: dict, language: str, rtl: bool) -> tuple[str, str, int]:
    """The hero text, its unit label, and the size it should be drawn at."""
    days = days_until(event)

    if days == 0:
        return t("card_today", language), "", 150
    if days < 0:
        return _digits(abs(days), rtl), t("card_days_ago", language), 268
    return _digits(days, rtl), t("card_days_left", language), 268


def render_event_card(event: dict, language: str = "en") -> bytes:
    """A square PNG of one event, ready to be posted into a chat."""
    rtl = language == "fa"
    kw = _kwargs(rtl)

    canvas = _gradient().convert("RGBA")
    _glow(canvas, 940, 110, 280, 30)
    _glow(canvas, 90, 1000, 220, 22)

    # A translucent panel over the gradient — the same material the Mini App
    # uses for its hero and sheets, so a shared card is recognisably the same
    # product rather than a generic export.
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    inset = 56
    box = (inset, inset, SIZE - inset, SIZE - inset - 60)
    pd.rounded_rectangle(box, radius=60, fill=(255, 255, 255, 20))
    pd.rounded_rectangle(box, radius=60, outline=(255, 255, 255, 58), width=3)
    canvas.alpha_composite(panel)

    d = ImageDraw.Draw(canvas)
    pad = inset + 58
    right = SIZE - pad
    mid = SIZE / 2
    edge = right if rtl else pad
    edge_anchor = "ra" if rtl else "la"

    # ── Category pill ────────────────────────────────────────────────
    # Solid, not translucent: white on translucent white had no contrast left
    # once the panel lightened the background underneath it.
    pill_font = _font("Bold", 32)
    label = _shape(category_label(event.get("category", "general"), language), rtl)
    pill_w = _measure(pill_font, label, rtl) + 60
    pill_x = right - pill_w if rtl else pad
    d.rounded_rectangle((pill_x, pad, pill_x + pill_w, pad + 64), radius=32,
                        fill=(255, 255, 255, 240))
    d.text((pill_x + pill_w / 2, pad + 34), label, font=pill_font, fill=PILL_TEXT,
           anchor="mm", **kw)

    # ── Countdown ────────────────────────────────────────────────────
    # Centred rather than aligned to the reading edge: right-aligning the whole
    # block leaves a dead quadrant on the opposite side, and a countdown reads
    # like a poster anyway, not like a paragraph.
    headline, unit, headline_size = _headline(event, language, rtl)
    d.text((mid, 300), _shape(headline, rtl), font=_font("Black", headline_size),
           fill=(255, 255, 255, 255), anchor="mm", **kw)

    if unit:
        d.text((mid, 466), _shape(unit, rtl), font=_font("Medium", 48),
               fill=(255, 255, 255, 225), anchor="mm", **kw)

    # ── Title ────────────────────────────────────────────────────────
    title_font = _font("Bold", 66)
    lines = _fit(str(event.get("title", "")).strip(), title_font, right - pad, 2, rtl)
    y = 572 if len(lines) == 2 else 596
    for text in lines:
        d.text((mid, y), _shape(text, rtl), font=title_font, fill=(255, 255, 255, 255),
               anchor="mm", **kw)
        y += 88

    # ── Divider and meta rows ────────────────────────────────────────
    # Pinned to fixed coordinates rather than flowing from the title: titles
    # are user input, a two-line one is the normal case, and letting the rows
    # flow pushed the last of them straight through the panel edge.
    d.line((pad, 742, right, 742), fill=(255, 255, 255, 70), width=2)

    rows = [
        (t("card_gregorian", language), str(event.get("date_iso", "") or "—")),
        (t("card_jalali", language), _digits(event.get("date_jalali", "") or "—", rtl)),
    ]
    if not event.get("all_day", True) and event.get("time_hm"):
        rows.append((t("card_time", language), _digits(event["time_hm"], rtl)))

    key_font = _font("Medium", 34)
    val_font = _font("Bold", 40)
    y = 790
    for key, value in rows:
        d.text((edge, y), _shape(key, rtl), font=key_font, fill=(255, 255, 255, 180),
               anchor=edge_anchor, **kw)
        d.text((pad if rtl else right, y), _shape(value, rtl), font=val_font,
               fill=(255, 255, 255, 255), anchor="la" if rtl else "ra", **kw)
        y += 60

    # ── Footer: the only reason this image is worth generating ───────
    handle = str(event.get("bot_handle") or "").strip()
    if handle:
        d.text((mid, SIZE - 60), handle, font=_font("Medium", 36),
               fill=(255, 255, 255, 215), anchor="mm")

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
'''

create("app/services/cards.py", CARDS_SERVICE, "share card renderer")

# ══════════════════════════════════════════════════════════════════════
# 4. Sharing state
# ══════════════════════════════════════════════════════════════════════

SHARING_SERVICE = '''from __future__ import annotations

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
'''

create("app/services/sharing.py", SHARING_SERVICE, "public sharing service")

# ══════════════════════════════════════════════════════════════════════
# 5. Bot API call that python-telegram-bot 21.1 does not have
# ══════════════════════════════════════════════════════════════════════

TELEGRAM_API_SERVICE = '''from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.telegram_api")

TIMEOUT = 15.0


def build_photo_result(card: str, link: str, title: str, caption: str,
                       button: str) -> dict:
    """An InlineQueryResultPhoto carrying the card and a way back to the bot.

    The button is not decoration: every shared card is supposed to be a door
    back into the bot, which is the entire reason Batch 12 exists.
    """
    return {
        "type": "photo",
        "id": "event-card",
        "photo_url": card,
        "thumbnail_url": card,
        "photo_width": 1080,
        "photo_height": 1080,
        "title": title,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": button, "url": link}]]},
    }


async def save_prepared_inline_message(
    user_id: str,
    result: dict,
    settings: Settings | None = None,
) -> str:
    """Wrap one inline result so the Mini App can hand it to shareMessage.

    Called over plain HTTP rather than through python-telegram-bot: this method
    arrived in Bot API 8.0 and the pinned 21.1 release does not expose it.
    Upgrading the library would touch the reminder worker and the webhook,
    which is a far larger blast radius than one POST.
    """
    settings = settings or get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/savePreparedInlineMessage"

    payload = {
        "user_id": int(user_id),
        "result": result,
        "allow_user_chats": True,
        "allow_group_chats": True,
        "allow_channel_chats": True,
        "allow_bot_chats": False,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(url, json=payload)

    try:
        body = response.json()
    except ValueError:
        logger.error("savePreparedInlineMessage returned non-JSON (%s)", response.status_code)
        raise RuntimeError("PREPARE_FAILED") from None

    if not body.get("ok"):
        # Old Bot API on a self-hosted server, or a photo URL Telegram could
        # not fetch. Either way the Mini App has a fallback, so this is logged
        # and reported rather than raised as a 500.
        logger.warning("savePreparedInlineMessage refused: %s", body.get("description"))
        raise RuntimeError("PREPARE_FAILED")

    return body["result"]["id"]
'''

create("app/services/telegram_api.py", TELEGRAM_API_SERVICE, "savePreparedInlineMessage helper")

# ══════════════════════════════════════════════════════════════════════
# 6. Routes
# ══════════════════════════════════════════════════════════════════════

SHARE_ROUTE = '''from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import Field

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.cards import FontsMissing, fonts_available, render_event_card
from app.services.reminders import event_language
from app.services.sharing import (
    card_url,
    get_public_event,
    get_share_state,
    miniapp_url,
    public_url,
    set_share_state,
)
from app.services.telegram_api import build_photo_result, save_prepared_inline_message
from app.utils.i18n import t

router = APIRouter(tags=["sharing"])
logger = logging.getLogger("tm_pro.share")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))

# The countdown moves once a day, so a long cache would serve a stale number
# and a zero-length one would re-render on every scroll past a preview.
CARD_CACHE = "public, max-age=900"


class SharePayload(InitDataPayload):
    event_id: str = Field(min_length=1, max_length=64)


class ShareTogglePayload(SharePayload):
    enabled: bool


@router.post("/api/share/state")
async def api_share_state(request: Request, payload: SharePayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)
    return await get_share_state(auth["user_id"], payload.event_id, settings)


@router.post("/api/share/toggle")
async def api_share_toggle(request: Request, payload: ShareTogglePayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)
    return await set_share_state(
        auth["user_id"], payload.event_id, payload.enabled, settings
    )


@router.post("/api/share/prepare")
async def api_share_prepare(request: Request, payload: SharePayload) -> dict:
    """Hand the Mini App a prepared message id for tg.shareMessage.

    Sharing the picture needs the picture to be reachable by Telegram's own
    servers, so this refuses politely when the event is not public yet rather
    than turning sharing on behind the owner's back.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    state = await get_share_state(auth["user_id"], payload.event_id, settings)
    if not state["enabled"]:
        raise HTTPException(status_code=400, detail="SHARING_DISABLED")
    if not fonts_available():
        raise HTTPException(status_code=503, detail="CARD_UNAVAILABLE")

    token = state["token"]
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    language = event_language(event)
    result = build_photo_result(
        card=card_url(token, settings),
        link=miniapp_url(token, settings),
        title=str(event.get("title", ""))[:120],
        caption=t("share_caption", language).format(
            title=str(event.get("title", ""))[:120]
        ),
        button=t("share_button", language),
    )

    try:
        prepared = await save_prepared_inline_message(auth["user_id"], result, settings)
    except Exception:
        # The Mini App falls back to a plain link share, so this is a normal
        # outcome on an older client rather than a server error.
        raise HTTPException(status_code=502, detail="PREPARE_FAILED") from None

    return {"success": True, "prepared_message_id": prepared}


@router.get("/c/{token}/card.png")
async def public_card(token: str) -> Response:
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    settings = get_settings()
    try:
        png = render_event_card(
            {**event, "bot_handle": f"@{settings.telegram_bot_username}"},
            event_language(event),
        )
    except FontsMissing:
        logger.error("static/fonts is empty — the share card cannot be rendered")
        raise HTTPException(status_code=503, detail="CARD_UNAVAILABLE") from None

    return Response(content=png, media_type="image/png", headers={"Cache-Control": CARD_CACHE})


@router.get("/c/{token}")
async def public_countdown(request: Request, token: str):
    """The public face of an event: one URL that behaves in both worlds.

    Inside Telegram the page bounces straight to the Mini App; in an ordinary
    browser it renders the card with a way in. The bounce is a best-effort
    user-agent check, so the button below it is always present and always
    works — nobody ends up staring at a page that did nothing.
    """
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    settings = get_settings()
    language = event_language(event)

    return templates.TemplateResponse(
        request,
        "countdown.html",
        {
            "lang": language,
            "direction": "rtl" if language == "fa" else "ltr",
            "title": str(event.get("title", "")),
            "card_url": card_url(token, settings),
            "page_url": public_url(token, settings),
            "miniapp_url": miniapp_url(token, settings),
            "bot_handle": f"@{settings.telegram_bot_username}",
            "open_label": t("share_open_button", language),
            "made_with": t("share_made_with", language),
        },
    )
'''

create("app/routes/share.py", SHARE_ROUTE, "sharing endpoints and public page")

COUNTDOWN_TEMPLATE = '''<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ direction }}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{{ title }}</title>

  <meta property="og:type" content="website" />
  <meta property="og:title" content="{{ title }}" />
  <meta property="og:url" content="{{ page_url }}" />
  <meta property="og:image" content="{{ card_url }}" />
  <meta property="og:image:width" content="1080" />
  <meta property="og:image:height" content="1080" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="{{ card_url }}" />
  <meta name="robots" content="noindex" />

  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100svh;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 24px;
      padding: 24px;
      background: radial-gradient(circle at 20% 0%, #4f46e5, #2e1065 70%);
      color: #fff;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }
    img {
      width: min(100%, 460px); height: auto;
      border-radius: 28px;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
    }
    a.cta {
      display: inline-flex; align-items: center; justify-content: center;
      min-height: 54px; padding: 0 32px;
      border-radius: 999px;
      background: #fff; color: #4f46e5;
      font-size: 1rem; font-weight: 700; text-decoration: none;
    }
    a.cta:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
    footer { font-size: 0.82rem; opacity: 0.7; text-align: center; }
  </style>
</head>
<body>
  <img src="{{ card_url }}" alt="{{ title }}" />
  <a class="cta" href="{{ miniapp_url }}">{{ open_label }}</a>
  <footer>{{ made_with }} · {{ bot_handle }}</footer>

  <script>
    // Telegram's Android in-app browser identifies itself; iOS usually does
    // not. So this is an opportunistic shortcut, never the only way through —
    // the button above is what everyone else uses.
    if (/Telegram/i.test(navigator.userAgent)) {
      window.location.replace("{{ miniapp_url }}");
    }
  </script>
</body>
</html>
'''

create("templates/countdown.html", COUNTDOWN_TEMPLATE, "public countdown page")

# ══════════════════════════════════════════════════════════════════════
# 7. Wiring
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/main.py",
    old="from app.routes.referral import router as referral_router\n",
    new=(
        "from app.routes.referral import router as referral_router\n"
        "from app.routes.share import router as share_router\n"
    ),
    marker="share_router",
    label="import share router",
)

patch(
    "app/main.py",
    old="app.include_router(referral_router)\n",
    new=(
        "app.include_router(referral_router)\n"
        "app.include_router(share_router)\n"
    ),
    marker="include_router(share_router)",
    label="register share router",
)

patch(
    "app/routes/web.py",
    old='    for name in ("style.css", "app.js", "referral.js"):\n',
    new='    for name in ("style.css", "app.js", "referral.js", "share.js"):\n',
    marker='"share.js"',
    label="cache-bust share.js",
)

patch(
    "templates/index.html",
    old='  <script src="/static/referral.js?v={{ asset_version }}" defer></script>\n',
    new=(
        '  <script src="/static/referral.js?v={{ asset_version }}" defer></script>\n'
        '  <script src="/static/share.js?v={{ asset_version }}" defer></script>\n'
    ),
    marker="share.js",
    label="load share.js",
)

# ══════════════════════════════════════════════════════════════════════
# 8. Copy
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/utils/i18n.py",
    old='    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},\n',
    new=(
        '    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},\n'
        '    "card_days_left": {"en": "days left", "fa": "روز مانده"},\n'
        '    "card_days_ago": {"en": "days ago", "fa": "روز گذشته"},\n'
        '    "card_today": {"en": "Today", "fa": "امروز"},\n'
        '    "card_gregorian": {"en": "Gregorian", "fa": "میلادی"},\n'
        '    "card_jalali": {"en": "Jalali", "fa": "شمسی"},\n'
        '    "card_time": {"en": "Time", "fa": "ساعت"},\n'
        '    "share_caption": {\n'
        '        "en": "<b>{title}</b> — counting down with TimeManager Pro.",\n'
        '        "fa": "<b>{title}</b> — شمارش معکوس با تایم‌منیجر پرو.",\n'
        "    },\n"
        '    "share_button": {"en": "Open in TimeManager", "fa": "باز کردن در تایم‌منیجر"},\n'
        '    "share_open_button": {"en": "Open in Telegram", "fa": "باز کردن در تلگرام"},\n'
        '    "share_made_with": {\n'
        '        "en": "Made with TimeManager Pro",\n'
        '        "fa": "ساخته‌شده با تایم‌منیجر پرو",\n'
        "    },\n"
    ),
    marker="card_days_left",
    label="card and sharing copy",
)

# ══════════════════════════════════════════════════════════════════════
# 9. Mini App — the share sheet
# ══════════════════════════════════════════════════════════════════════

SHARE_JS = r'''/* ──────────────────────────────────────────────────────────────
   share.js — Batch 12b, the share sheet.

   Standalone like referral.js: it builds its own markup and reads what it
   needs from Telegram directly, so app.js keeps working untouched if this
   file ever fails to load. app.js calls window.TMShare.open(event) and
   falls back to its old text share when this object is absent.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "Share event": "اشتراک‌گذاری رویداد",
    "Close": "بستن",
    "Public link": "لینک عمومی",
    "Anyone with the link can see this event's title and date.":
      "هر کسی که این لینک را داشته باشد، عنوان و تاریخ این رویداد را می‌بیند.",
    "Turn on public sharing?": "اشتراک‌گذاری عمومی روشن شود؟",
    "The title, date and countdown of this event become visible to anyone who opens the link. You can switch it off at any time, and the old link stops working.":
      "عنوان، تاریخ و شمارش معکوس این رویداد برای هر کسی که لینک را باز کند دیده می‌شود. هر وقت بخواهی می‌توانی خاموشش کنی و آن‌وقت لینک قبلی دیگر کار نمی‌کند.",
    "Turn on": "روشن کن",
    "Cancel": "انصراف",
    "Send in Telegram": "ارسال در تلگرام",
    "Copy link": "کپی لینک",
    "Copied": "کپی شد",
    "Turn on sharing to send the card.": "برای ارسال کارت، اشتراک‌گذاری را روشن کن.",
    "Could not load sharing.": "وضعیت اشتراک‌گذاری بارگذاری نشد.",
    "Something went wrong.": "مشکلی پیش آمد.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text) {
    return isFa && FA[text] ? FA[text] : text;
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (kind === "warning") tg.HapticFeedback.notificationOccurred("warning");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  var els = {};
  var current = null;
  var state = null;

  async function api(path, body) {
    var response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ initData: (tg && tg.initData) || "" }, body)),
    });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  /* ── Markup ─────────────────────────────────────────── */

  function build() {
    if (els.overlay) return;

    var overlay = document.createElement("div");
    overlay.className = "shr-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="shr-dialog" role="dialog" aria-modal="true" aria-labelledby="shrTitle">' +
        '<div class="shr-handle" aria-hidden="true"></div>' +
        '<div class="shr-head">' +
          '<h2 class="shr-title" id="shrTitle">' + t("Share event") + "</h2>" +
          '<button type="button" class="icon-btn shr-close" id="shrClose" aria-label="' +
            t("Close") + '">✕</button>' +
        "</div>" +
        '<div class="shr-preview" id="shrPreview"><div class="shr-skeleton"></div></div>' +
        '<div class="shr-toggle-row">' +
          '<div class="shr-toggle-copy">' +
            "<strong>" + t("Public link") + "</strong>" +
            "<p>" + t("Anyone with the link can see this event's title and date.") + "</p>" +
          "</div>" +
          '<button type="button" class="shr-switch" id="shrSwitch" role="switch" ' +
            'aria-checked="false"><span class="shr-knob"></span></button>' +
        "</div>" +
        '<p class="shr-hint" id="shrHint"></p>' +
        '<div class="shr-actions">' +
          '<button type="button" class="btn-secondary" id="shrCopy">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="shrSend">' + t("Send in Telegram") + "</button>" +
        "</div>" +
      "</div>";

    document.body.appendChild(overlay);
    if (isFa) overlay.setAttribute("dir", "rtl");

    els.overlay = overlay;
    els.preview = overlay.querySelector("#shrPreview");
    els.switch = overlay.querySelector("#shrSwitch");
    els.hint = overlay.querySelector("#shrHint");
    els.copy = overlay.querySelector("#shrCopy");
    els.send = overlay.querySelector("#shrSend");

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    overlay.querySelector("#shrClose").addEventListener("click", close);
    els.switch.addEventListener("click", onToggle);
    els.copy.addEventListener("click", copyLink);
    els.send.addEventListener("click", sendCard);
  }

  function confirmPublic() {
    // The owner's own decision to make, so it is asked once, in plain words,
    // at the moment it takes effect — not buried in a settings screen.
    return new Promise(function (resolve) {
      var box = document.createElement("div");
      box.className = "shr-confirm";
      if (isFa) box.setAttribute("dir", "rtl");
      box.innerHTML =
        '<div class="shr-confirm-card">' +
          "<strong>" + t("Turn on public sharing?") + "</strong>" +
          "<p>" + t("The title, date and countdown of this event become visible to anyone who opens the link. You can switch it off at any time, and the old link stops working.") + "</p>" +
          '<div class="shr-confirm-actions">' +
            '<button type="button" class="btn-secondary" data-answer="no">' + t("Cancel") + "</button>" +
            '<button type="button" class="btn-primary" data-answer="yes">' + t("Turn on") + "</button>" +
          "</div>" +
        "</div>";

      box.addEventListener("click", function (event) {
        var answer = event.target.getAttribute("data-answer");
        if (!answer) return;
        box.remove();
        resolve(answer === "yes");
      });

      document.body.appendChild(box);
    });
  }

  /* ── Rendering ──────────────────────────────────────── */

  function render() {
    if (!state) return;

    var on = !!state.enabled;
    els.switch.setAttribute("aria-checked", on ? "true" : "false");
    els.switch.classList.toggle("is-on", on);

    els.copy.disabled = !on;
    els.send.disabled = !on;
    els.hint.textContent = on ? "" : t("Turn on sharing to send the card.");

    if (on && state.card_url) {
      // Cache-busted per open so the day count in the picture is never the
      // one from yesterday's visit.
      els.preview.innerHTML =
        '<img alt="" src="' + state.card_url + "?t=" + Date.now() + '" />';
    } else {
      els.preview.innerHTML = '<div class="shr-skeleton"></div>';
    }
  }

  /* ── Actions ────────────────────────────────────────── */

  async function onToggle() {
    if (!current || !state) return;

    var next = !state.enabled;
    if (next && !(await confirmPublic())) return;

    haptic(next ? "success" : "warning");
    els.switch.disabled = true;

    try {
      state = await api("/api/share/toggle", { event_id: current, enabled: next });
      render();
    } catch (_) {
      els.hint.textContent = t("Something went wrong.");
    } finally {
      els.switch.disabled = false;
    }
  }

  function flash(button, text) {
    var original = button.textContent;
    button.textContent = text;
    setTimeout(function () {
      button.textContent = original;
    }, 1500);
  }

  function copyLink() {
    if (!state || !state.public_url) return;
    haptic("success");

    var done = function () {
      flash(els.copy, t("Copied"));
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(state.public_url).then(done, done);
    } else {
      done();
    }
  }

  function shareViaLink() {
    // Works on every client: the public URL carries Open Graph tags pointing
    // at the same card, so the chat preview still shows the picture.
    var url =
      "https://t.me/share/url?url=" + encodeURIComponent(state.public_url);
    if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  async function sendCard() {
    if (!state || !state.enabled) return;
    haptic("success");

    if (!tg || typeof tg.shareMessage !== "function") {
      shareViaLink();
      return;
    }

    els.send.disabled = true;
    try {
      var prepared = await api("/api/share/prepare", { event_id: current });
      tg.shareMessage(prepared.prepared_message_id);
    } catch (_) {
      // Bot API too old, or Telegram could not fetch the photo. Either way the
      // person still gets to share something.
      shareViaLink();
    } finally {
      els.send.disabled = false;
    }
  }

  function close() {
    if (!els.overlay) return;
    els.overlay.classList.remove("is-open");
    setTimeout(function () {
      els.overlay.hidden = true;
    }, 200);
  }

  async function open(event) {
    if (!event || !event.id) return;
    haptic();
    build();

    current = event.id;
    state = null;
    els.preview.innerHTML = '<div class="shr-skeleton"></div>';
    els.hint.textContent = "";
    els.overlay.hidden = false;
    requestAnimationFrame(function () {
      els.overlay.classList.add("is-open");
    });

    try {
      state = await api("/api/share/state", { event_id: current });
      render();
    } catch (_) {
      els.hint.textContent = t("Could not load sharing.");
    }
  }

  window.TMShare = { open: open };
})();
'''

create("static/share.js", SHARE_JS, "share sheet")

patch(
    "static/app.js",
    old=(
        "  async function shareCurrentEvent() {\n"
        "    const ev = getEventById(state.detailEventId);\n"
        "    if (!ev) return;\n"
        "\n"
        "    const text = [\n"
    ),
    new=(
        "  async function shareCurrentEvent() {\n"
        "    const ev = getEventById(state.detailEventId);\n"
        "    if (!ev) return;\n"
        "\n"
        "    // Batch 12b: the picture card and the public link live in share.js.\n"
        "    // The plain-text share below stays as the fallback for the case\n"
        "    // where that file failed to load.\n"
        "    if (window.TMShare && typeof window.TMShare.open === \"function\") {\n"
        "      window.TMShare.open(ev);\n"
        "      return;\n"
        "    }\n"
        "\n"
        "    const text = [\n"
    ),
    marker="window.TMShare",
    label="route Share to the new sheet",
)

SHARE_CSS = r'''
/* ── Batch 12b — Share sheet ─────────────────────────── */
.shr-overlay {
  position: fixed; inset: 0; z-index: 70;
  display: flex; align-items: flex-end; justify-content: center;
  background: rgba(10, 12, 30, 0.5);
  backdrop-filter: blur(3px);
  opacity: 0;
  transition: opacity 200ms ease;
}
.shr-overlay.is-open { opacity: 1; }

.shr-dialog {
  width: min(100%, var(--app-max, 560px));
  max-height: 92svh; overflow-y: auto;
  padding: 10px 20px calc(24px + env(safe-area-inset-bottom, 0px));
  background: var(--surface);
  border-radius: var(--r-xl) var(--r-xl) 0 0;
  border: 1px solid var(--border);
  transform: translateY(16px);
  transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
}
.shr-overlay.is-open .shr-dialog { transform: translateY(0); }

.shr-handle {
  width: 40px; height: 4px; margin: 6px auto 14px;
  border-radius: var(--r-pill); background: var(--border);
}
.shr-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.shr-title { margin: 0; font-size: 1.05rem; font-weight: 800; }
.shr-close { width: 36px; height: 36px; font-size: 0.9rem; }

.shr-preview {
  margin: 16px 0;
  border-radius: var(--r-lg);
  overflow: hidden;
  background: var(--surface-2);
  aspect-ratio: 1 / 1;
}
.shr-preview img { display: block; width: 100%; height: 100%; object-fit: cover; }

.shr-skeleton {
  width: 100%; height: 100%;
  background: linear-gradient(100deg, var(--surface-2) 30%, var(--border) 50%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: shr-shimmer 1.4s linear infinite;
}
@keyframes shr-shimmer {
  from { background-position: 180% 0; }
  to { background-position: -80% 0; }
}

.shr-toggle-row {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px;
  border-radius: var(--r-md);
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.shr-toggle-copy { flex: 1; min-width: 0; }
.shr-toggle-copy strong { display: block; font-size: 0.92rem; font-weight: 800; }
.shr-toggle-copy p { margin: 4px 0 0; font-size: 0.8rem; color: var(--text-muted); line-height: 1.5; }

.shr-switch {
  flex-shrink: 0;
  width: 56px; height: 32px; padding: 3px;
  border: none; border-radius: var(--r-pill);
  background: var(--border);
  cursor: pointer;
  transition: background 180ms ease;
}
.shr-switch.is-on { background: var(--brand); }
.shr-switch:disabled { opacity: 0.6; }
.shr-knob {
  display: block; width: 26px; height: 26px;
  border-radius: 50%; background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  transition: transform 180ms cubic-bezier(0.22, 1, 0.36, 1);
}
.shr-switch.is-on .shr-knob { transform: translateX(24px); }
[dir="rtl"] .shr-switch.is-on .shr-knob { transform: translateX(-24px); }

.shr-hint { margin: 10px 2px 0; font-size: 0.8rem; color: var(--text-muted); }
.shr-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
.shr-actions button:disabled { opacity: 0.5; }

.shr-confirm {
  position: fixed; inset: 0; z-index: 80;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: rgba(10, 12, 30, 0.6);
}
.shr-confirm-card {
  width: min(100%, 380px);
  padding: 22px;
  border-radius: var(--r-lg);
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}
.shr-confirm-card strong { display: block; font-size: 1rem; font-weight: 800; }
.shr-confirm-card p { margin: 8px 0 0; font-size: 0.85rem; color: var(--text-muted); line-height: 1.6; }
.shr-confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 18px; }

@media (prefers-reduced-motion: reduce) {
  .shr-overlay, .shr-dialog, .shr-knob, .shr-skeleton { transition: none; animation: none; }
}
'''

append("static/style.css", SHARE_CSS, "Batch 12b — Share sheet", "share sheet styles")

# ══════════════════════════════════════════════════════════════════════
# 10. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.cards import days_until, fonts_available, render_event_card
from app.services.sharing import (
    EVENT_PREFIX,
    card_url,
    generate_public_token,
    miniapp_url,
    parse_event_payload,
    public_url,
)
from app.services.telegram_api import build_photo_result

SETTINGS = SimpleNamespace(
    webapp_base_url="https://tm.example.com",
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)

needs_fonts = pytest.mark.skipif(
    not fonts_available(),
    reason="static/fonts is empty — run apply_batch12b.py or fetch Vazirmatn",
)


def _event(**overrides) -> dict:
    base = {
        "title": "Mum's birthday",
        "date_iso": "2026-10-20",
        "date_jalali": "1405/07/28",
        "category": "birthday",
        "all_day": True,
        "tz_name": "Europe/Berlin",
        "lang": "en",
        "bot_handle": "@Timemanager2026_bot",
    }
    base.update(overrides)
    return base


# ── Tokens and links ─────────────────────────────────────────────────

def test_tokens_are_long_and_unique() -> None:
    tokens = {generate_public_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 16
        assert all(char.isalnum() or char in "-_" for char in token)


def test_link_shapes() -> None:
    assert public_url("abc123DEF456ghi789", SETTINGS) == (
        "https://tm.example.com/c/abc123DEF456ghi789"
    )
    assert card_url("abc123DEF456ghi789", SETTINGS).endswith("/card.png")
    assert miniapp_url("abc123DEF456ghi789", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=e_abc123DEF456ghi789"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (EVENT_PREFIX + "abc123DEF456ghi789", "abc123DEF456ghi789"),
        ("e_abc123DEF456ghi789", "abc123DEF456ghi789"),
        ("r_7KQ2M9XA", None),  # a Batch 12a referral payload, not ours
        ("e_short", None),
        ("e_bad token here", None),
        ("", None),
        (None, None),
    ],
)
def test_only_our_own_event_payloads_resolve(payload, expected) -> None:
    assert parse_event_payload(payload) == expected


# ── Countdown maths ──────────────────────────────────────────────────

def _now(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("today", "expected"),
    [("2026-10-18T09:00", 2), ("2026-10-20T09:00", 0), ("2026-10-25T09:00", -5)],
)
def test_days_until_counts_calendar_days(today, expected) -> None:
    assert days_until(_event(), _now(today)) == expected


def test_a_late_evening_event_is_still_tomorrow() -> None:
    """Comparing instants would call 23:30 tomorrow "0 days" before midnight."""
    event = _event(date_iso="2026-10-20", all_day=False, time_hm="23:30")

    assert days_until(event, _now("2026-10-19T00:30")) == 1


def test_the_next_occurrence_wins_over_the_original_date() -> None:
    """For a repeating event, event_ts_utc already holds the next one."""
    event = _event(
        date_iso="2020-10-20",
        repeat="yearly",
        event_ts_utc=datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc),
    )

    assert days_until(event, _now("2026-10-13T09:00")) == 7


def test_a_broken_date_does_not_take_the_card_down() -> None:
    assert days_until(_event(date_iso="not-a-date")) == 0


# ── The card itself ──────────────────────────────────────────────────

PNG_MAGIC = b"\\x89PNG\\r\\n\\x1a\\n"


@needs_fonts
@pytest.mark.parametrize("language", ["en", "fa"])
def test_card_renders_a_real_png(language) -> None:
    png = render_event_card(_event(), language)

    assert png.startswith(PNG_MAGIC)
    assert len(png) > 10_000


@needs_fonts
def test_a_very_long_title_is_truncated_rather_than_overflowing() -> None:
    long_title = "Quarterly planning review with the platform and infrastructure teams " * 3

    assert render_event_card(_event(title=long_title), "en").startswith(PNG_MAGIC)


@needs_fonts
@pytest.mark.parametrize(
    "event",
    [
        _event(title=""),
        _event(date_jalali=""),
        _event(all_day=False, time_hm="18:30"),
        _event(event_ts_utc=datetime.now(timezone.utc) + timedelta(days=400)),
        _event(event_ts_utc=datetime.now(timezone.utc) - timedelta(days=3)),
        _event(category="unknown-category"),
    ],
)
def test_awkward_events_still_produce_a_card(event) -> None:
    assert render_event_card(event, "fa").startswith(PNG_MAGIC)


# ── The inline result handed to Telegram ─────────────────────────────

def test_every_shared_card_carries_a_way_back_to_the_bot() -> None:
    result = build_photo_result(
        card="https://tm.example.com/c/tok/card.png",
        link="https://t.me/Timemanager2026_bot/app?startapp=e_tok",
        title="Mum's birthday",
        caption="<b>Mum's birthday</b>",
        button="Open in TimeManager",
    )

    assert result["type"] == "photo"
    assert result["photo_url"].endswith("/card.png")
    assert result["thumbnail_url"] == result["photo_url"]

    # The whole point of Batch 12: a shared card is a door back in.
    button = result["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"].startswith("https://t.me/")
    assert "startapp=" in button["url"]
'''

create("tests/test_sharing.py", TESTS, "sharing test suite")

patch(
    "README.md",
    old="| `EVENT_LIMIT_BASE`, `REFERRAL_STEP`, `REFERRAL_BONUS` | Referral reward: "
        "start at 20 events, +20 per 3 valid invites |\n",
    new=(
        "| `EVENT_LIMIT_BASE`, `REFERRAL_STEP`, `REFERRAL_BONUS` | Referral reward: "
        "start at 20 events, +20 per 3 valid invites |\n"
        "| `WEBAPP_BASE_URL` | Also the origin of public countdown links "
        "(`/c/<token>`) and share cards |\n"
    ),
    marker="public countdown links",
    label="document the public link origin",
)


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 12b (share card, public countdown link)\n")
    print("  " + "─" * min(width + 8, 76))
    for status, message in LOG:
        print(f"  [{status}] {message}")
    print("  " + "─" * min(width + 8, 76))

    for note in NOTES:
        print(f"\n  ! {note}")

    if FAILED:
        print("\n  Nothing was written. Fix the files named above and run again.\n")
        return 1

    total = len(PENDING) + len(BINARY)
    if DRY_RUN:
        print(f"\n  Dry run: {total} file(s) would change. Nothing written.\n")
        return 0

    flush()
    print(f"\n  {total} file(s) written. Next:\n")
    print("      pip install -r requirements.txt   # Pillow is new")
    print("      ruff check . && pytest")
    print("      git add -A && git commit -m 'Batch 12b: share cards and public countdown links'")
    print()
    print("  Commit static/fonts/ too — the card renderer needs it at runtime,")
    print("  and without it CI skips the card tests instead of running them.")
    print("  Restart the web app so ensure_indexes() creates the public_token index.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
