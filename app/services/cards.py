from __future__ import annotations

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
