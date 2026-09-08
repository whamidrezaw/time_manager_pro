#!/usr/bin/env python3
"""
apply_batch13b.py — TimeManager Pro, Batch 13b (month view, year grid)

  * app/utils/occurrences.py — expands a repeating event across a date range
  * POST /api/calendar — per-day counts, plus the events themselves for a month
  * a three-tab bottom bar: list, month, year
  * static/views.js — the month grid, the year grid and the day sheet

Run once from the repository root:

    python apply_batch13b.py --check   # dry run, writes nothing
    python apply_batch13b.py           # apply

Requires Batch 13a-2. Safe to run twice.
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
              "— is Batch 13a-2 applied?")
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
# 1. Expanding a repeating event across a range
# ══════════════════════════════════════════════════════════════════════

OCCURRENCES = '''from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app.utils.dates import (
    _past_until,
    advance_occurrence,
    build_occurrence,
    safe_zoneinfo,
)

logger = logging.getLogger("tm_pro.occurrences")

# A daily event across a full year is 365 of these; the cap is what stops a
# corrupt repeat value from turning one request into an endless loop.
MAX_STEPS = 800


def _fast_forward(occurrence: datetime, repeat: str, tz, start: date) -> datetime:
    """Skip the repeats that fall entirely before the window.

    Only daily and weekly get this. Both are plain wall-clock deltas, so
    jumping k periods at once lands exactly where k single steps would, and
    the calendar-sensitive cases — month lengths, 29 February — stay the sole
    responsibility of advance_occurrence.
    """
    days = {"daily": 1, "weekly": 7}.get(repeat)
    if not days:
        return occurrence

    local = occurrence.astimezone(tz).replace(tzinfo=None)
    behind = (start - local.date()).days
    if behind <= 0:
        return occurrence

    local += timedelta(days=(behind // days) * days)
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def expand_occurrences(event: dict, start: date, end: date,
                       max_steps: int = MAX_STEPS) -> list[date]:
    """Every day this event lands on between start and end, inclusive.

    The stored event only knows its next occurrence, which is enough for a
    reminder and not enough for a calendar: a monthly event would draw one dot
    a year. So the series is walked here — but every step is taken by
    advance_occurrence, the same function the reminder worker uses. A calendar
    that disagreed with the reminders would be worse than no calendar.

    (_past_until is private to app.utils.dates. It is imported rather than
    rewritten because "has this series ended" is exactly the kind of rule that
    must not exist twice.)
    """
    date_iso = str(event.get("date_iso") or "")
    if not date_iso or end < start:
        return []

    tz, _ = safe_zoneinfo(event.get("tz_name"))
    try:
        occurrence = build_occurrence(
            date_iso, tz, bool(event.get("all_day", True)), event.get("time_hm")
        )
    except ValueError:
        logger.warning("unparseable date_iso on event %s", event.get("_id"))
        return []

    repeat = str(event.get("repeat") or "none")
    if repeat == "none":
        only = occurrence.astimezone(tz).date()
        return [only] if start <= only <= end else []

    until = event.get("repeat_until")
    anchor_day = int(date_iso[8:10] or 1)
    occurrence = _fast_forward(occurrence, repeat, tz, start)

    found: list[date] = []
    for _ in range(max_steps):
        local = occurrence.astimezone(tz).date()
        if local > end or _past_until(occurrence, tz, until):
            return found
        if local >= start:
            found.append(local)

        nxt = advance_occurrence(occurrence, repeat, tz, anchor_day)
        if nxt is None:
            return found
        occurrence = nxt

    logger.error("expand_occurrences hit the step cap (repeat=%s)", repeat)
    return found
'''

create("app/utils/occurrences.py", OCCURRENCES, "occurrence expansion")

# ══════════════════════════════════════════════════════════════════════
# 2. The calendar endpoint
# ══════════════════════════════════════════════════════════════════════

CALENDAR_ROUTE = '''from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.config import get_settings
from app.db import get_events_collection
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, validate_init_data
from app.utils.occurrences import expand_occurrences

router = APIRouter(tags=["calendar"])
logger = logging.getLogger("tm_pro.calendar")

# A year of the pixel grid is 371 days; anything past that is not a view the
# Mini App has, so it is a malformed request rather than a big one.
MAX_RANGE_DAYS = 400

# The month view wants the events themselves so a tapped day can list them
# without a second round trip. The year grid only ever draws counts, and
# shipping a year of titles to colour 371 squares would be pure waste.
ITEMS_RANGE_DAYS = 62
MAX_ITEMS = 400

PROJECTION = {
    "title": 1, "date_iso": 1, "tz_name": 1, "all_day": 1, "time_hm": 1,
    "repeat": 1, "repeat_until": 1, "category": 1, "pinned": 1,
}


class CalendarPayload(InitDataPayload):
    start: str = Field(min_length=10, max_length=10)
    end: str = Field(min_length=10, max_length=10)


@router.post("/api/calendar")
async def api_calendar(request: Request, payload: CalendarPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    try:
        start = date.fromisoformat(payload.start)
        end = date.fromisoformat(payload.end)
    except ValueError:
        raise HTTPException(status_code=400, detail="INVALID_DATE_FORMAT") from None

    if end < start:
        raise HTTPException(status_code=400, detail="INVALID_RANGE")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="RANGE_TOO_LARGE")

    want_items = (end - start).days <= ITEMS_RANGE_DAYS
    days: dict[str, int] = {}
    items: list[dict] = []

    cursor = get_events_collection().find({"user_id": auth["user_id"]}, PROJECTION)
    async for doc in cursor:
        for when in expand_occurrences(doc, start, end):
            key = when.isoformat()
            days[key] = days.get(key, 0) + 1

            if want_items and len(items) < MAX_ITEMS:
                items.append({
                    "id": str(doc["_id"]),
                    "date": key,
                    "title": doc.get("title", ""),
                    "category": doc.get("category", "general"),
                    "pinned": bool(doc.get("pinned")),
                    "all_day": bool(doc.get("all_day", True)),
                    "time_hm": doc.get("time_hm"),
                })

    items.sort(key=lambda item: (item["date"], item["title"]))
    return {"success": True, "days": days, "items": items,
            "start": payload.start, "end": payload.end}
'''

create("app/routes/calendar.py", CALENDAR_ROUTE, "calendar endpoint")

patch(
    "app/main.py",
    old="from app.routes.events import router as events_router\n",
    new=(
        "from app.routes.calendar import router as calendar_router\n"
        "from app.routes.events import router as events_router\n"
    ),
    marker="calendar_router",
    label="import calendar router",
)

patch(
    "app/main.py",
    old="app.include_router(events_router)\n",
    new=(
        "app.include_router(events_router)\n"
        "app.include_router(calendar_router)\n"
    ),
    marker="include_router(calendar_router)",
    label="register calendar router",
)

patch(
    "app/routes/web.py",
    old='    for name in ("style.css", "app.js", "referral.js", "share.js"):\n',
    new='    for name in ("style.css", "app.js", "referral.js", "share.js", "views.js"):\n',
    marker='"views.js"',
    label="cache-bust views.js",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Markup: view containers and the tab bar
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""  </main>
""",
    new="""  </main>

  <main id="monthView" class="app-main view-pane" hidden></main>
  <main id="yearView" class="app-main view-pane" hidden></main>

  <nav class="tabbar" id="tabbar" aria-label="Views">
    <button type="button" class="tab is-active" data-view="list" aria-current="page">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
      <span data-i18n>List</span>
    </button>
    <button type="button" class="tab" data-view="month">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
      <span data-i18n>Month</span>
    </button>
    <button type="button" class="tab" data-view="year">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      <span data-i18n>Year</span>
    </button>
  </nav>
""",
    marker='id="tabbar"',
    label="view panes and tab bar",
)

patch(
    "templates/index.html",
    old='  <script src="/static/share.js?v={{ asset_version }}" defer></script>\n',
    new=(
        '  <script src="/static/share.js?v={{ asset_version }}" defer></script>\n'
        '  <script src="/static/views.js?v={{ asset_version }}" defer></script>\n'
    ),
    marker="views.js",
    label="load views.js",
)

# ══════════════════════════════════════════════════════════════════════
# 4. A narrow seam out of app.js
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""  /* ── Boot ────────────────────────────────────────────── */
""",
    new="""  /* ── Published surface ───────────────────────────────
     views.js lives outside this closure and needs three things from it: a way
     into the detail sheet, the loaded events, and the Jalali conversion. The
     last one matters most — a second date conversion in another file is how
     two parts of the same app start disagreeing about what day it is. */
  window.TMApp = {
    openDetail,
    getEvent: getEventById,
    reload: loadEvents,
    language: () => currentLang,
    jalali: { fromGregorian: gregorianToJalali, toGregorian: jalaliToGregorian,
              daysInMonth: daysInJalaliMonth },
  };

  /* ── Boot ────────────────────────────────────────────── */
""",
    marker="window.TMApp = {",
    label="expose the seam for views.js",
)

# ══════════════════════════════════════════════════════════════════════
# 5. The views
# ══════════════════════════════════════════════════════════════════════

VIEWS_JS = r'''/* ──────────────────────────────────────────────────────────────
   views.js — Batch 13b: the month grid, the year grid, the day sheet.

   Standalone like referral.js and share.js. Everything it needs from the
   main closure comes through window.TMApp, and if that is missing the tab
   bar simply never appears — the list keeps working on its own.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "List": "لیست", "Month": "ماه", "Year": "سال",
    "No events on this day.": "این روز رویدادی ندارد.",
    "Close": "بستن",
    "Could not load the calendar.": "تقویم بارگذاری نشد.",
    "Today": "امروز",
    "Less": "کمتر", "More": "بیشتر",
    "all day": "تمام‌روز",
  };

  var JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                       "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
  var GREGORIAN_MONTHS = ["January", "February", "March", "April", "May", "June",
                          "July", "August", "September", "October", "November", "December"];
  var JALALI_DOW = ["ش", "ی", "د", "س", "چ", "پ", "ج"];
  var GREGORIAN_DOW = ["M", "T", "W", "T", "F", "S", "S"];

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text) { return isFa && FA[text] ? FA[text] : text; }

  function num(value) {
    var s = String(value);
    return isFa ? s.replace(/[0-9]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[+d]; }) : s;
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "select") tg.HapticFeedback.selectionChanged();
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  function iso(y, m, d) {
    return y + "-" + String(m).padStart(2, "0") + "-" + String(d).padStart(2, "0");
  }

  function todayParts() {
    var now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
  }

  /* ── Calendar maths ─────────────────────────────────
     Persian users get a Jalali year, everyone else a Gregorian one. The
     conversion itself is borrowed from app.js rather than written again. */

  var J = (window.TMApp && window.TMApp.jalali) || null;

  function gregorianDaysInMonth(y, m) { return new Date(y, m, 0).getDate(); }

  function monthMeta(y, m) {
    if (isFa && J) {
      var first = J.toGregorian(y, m, 1);
      var start = new Date(first.gy, first.gm - 1, first.gd);
      return {
        label: JALALI_MONTHS[m - 1] + " " + num(y),
        days: J.daysInMonth(y, m),
        // Saturday starts the Persian week.
        offset: (start.getDay() + 1) % 7,
        toIso: function (day) {
          var g = J.toGregorian(y, m, day);
          return iso(g.gy, g.gm, g.gd);
        },
      };
    }
    var firstG = new Date(y, m - 1, 1);
    return {
      label: GREGORIAN_MONTHS[m - 1] + " " + y,
      days: gregorianDaysInMonth(y, m),
      offset: (firstG.getDay() + 6) % 7,
      toIso: function (day) { return iso(y, m, day); },
    };
  }

  function nowInCalendar() {
    var g = todayParts();
    if (isFa && J) {
      var j = J.fromGregorian(g.y, g.m, g.d);
      return { y: j.jy, m: j.jm, d: j.jd };
    }
    return g;
  }

  function stepMonth(y, m, delta) {
    var total = (m - 1) + delta;
    return { y: y + Math.floor(total / 12), m: ((total % 12) + 12) % 12 + 1 };
  }

  /* ── Data ───────────────────────────────────────────── */

  var cache = {};

  async function fetchRange(start, end) {
    var key = start + ":" + end;
    if (cache[key]) return cache[key];

    var response = await fetch("/api/calendar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: (tg && tg.initData) || "", start: start, end: end }),
    });
    if (!response.ok) throw new Error(String(response.status));

    cache[key] = await response.json();
    return cache[key];
  }

  /* ── Month view ─────────────────────────────────────── */

  var cursor = null;
  var els = {};

  function renderMonth() {
    var meta = monthMeta(cursor.y, cursor.m);
    var today = nowInCalendar();
    var dow = isFa ? JALALI_DOW : GREGORIAN_DOW;

    var html = '<div class="cal-head">'
      + '<button type="button" class="icon-btn" data-step="-1" aria-label="Previous month">‹</button>'
      + '<span class="cal-title">' + meta.label + "</span>"
      + '<button type="button" class="icon-btn" data-step="1" aria-label="Next month">›</button>'
      + "</div><div class=\"cal-dow\">";
    dow.forEach(function (name) { html += "<span>" + name + "</span>"; });
    html += '</div><div class="cal-grid" id="calGrid">';
    for (var i = 0; i < meta.offset; i++) html += "<span></span>";
    for (var day = 1; day <= meta.days; day++) {
      var dayIso = meta.toIso(day);
      var isToday = day === today.d && cursor.m === today.m && cursor.y === today.y;
      html += '<button type="button" class="cal-day' + (isToday ? " is-today" : "")
        + '" data-iso="' + dayIso + '"><span class="cal-num">' + num(day)
        + '</span><span class="cal-dots" data-dots="' + dayIso + '"></span></button>';
    }
    html += "</div>";

    els.month.innerHTML = html;
    els.month.querySelectorAll("[data-step]").forEach(function (button) {
      button.addEventListener("click", function () {
        haptic("select");
        var next = stepMonth(cursor.y, cursor.m, Number(button.dataset.step));
        cursor = { y: next.y, m: next.m };
        renderMonth();
      });
    });
    els.month.querySelectorAll(".cal-day").forEach(function (button) {
      button.addEventListener("click", function () { openDay(button.dataset.iso); });
    });

    paintMonth(meta);
  }

  async function paintMonth(meta) {
    var start = meta.toIso(1);
    var end = meta.toIso(meta.days);
    try {
      var data = await fetchRange(start, end);
      monthItems = data.items || [];
      Object.keys(data.days || {}).forEach(function (key) {
        var slot = els.month.querySelector('[data-dots="' + key + '"]');
        if (!slot) return;
        var count = Math.min(data.days[key], 3);
        var dots = "";
        for (var i = 0; i < count; i++) dots += "<span></span>";
        slot.innerHTML = dots;
      });
    } catch (_) {
      /* A calendar without dots is still a calendar. */
    }
  }

  var monthItems = [];

  /* ── Year grid ──────────────────────────────────────── */

  async function renderYear() {
    var today = nowInCalendar();
    var first = isFa && J ? J.toGregorian(today.y, 1, 1) : { gy: today.y, gm: 1, gd: 1 };
    var after = isFa && J ? J.toGregorian(today.y + 1, 1, 1) : { gy: today.y + 1, gm: 1, gd: 1 };
    var startDate = new Date(first.gy, first.gm - 1, first.gd);
    // Measured, not assumed: a Jalali year is 365 or 366 days and so is a
    // Gregorian one, and one square too many is a square from next year.
    var days = Math.round(
      (new Date(after.gy, after.gm - 1, after.gd) - startDate) / 86400000
    );

    var startIso = iso(startDate.getFullYear(), startDate.getMonth() + 1, startDate.getDate());
    var endDate = new Date(startDate);
    endDate.setDate(endDate.getDate() + days - 1);
    var endIso = iso(endDate.getFullYear(), endDate.getMonth() + 1, endDate.getDate());

    var todayIso = (function () { var g = todayParts(); return iso(g.y, g.m, g.d); })();

    var counts = {};
    try {
      // The range runs past the endpoint's item limit, so only counts come
      // back here — which is all 371 squares can show anyway.
      var data = await fetchRange(startIso, endIso);
      counts = data.days || {};
    } catch (_) {
      els.year.innerHTML = '<p class="cal-error">' + t("Could not load the calendar.") + "</p>";
      return;
    }

    var cells = "";
    var walk = new Date(startDate);
    for (var i = 0; i < days; i++) {
      var key = iso(walk.getFullYear(), walk.getMonth() + 1, walk.getDate());
      var level = Math.min(counts[key] || 0, 4);
      cells += '<button type="button" class="px lv' + level
        + (key === todayIso ? " is-today" : "")
        + '" data-iso="' + key + '" aria-label="' + key + '"></button>';
      walk.setDate(walk.getDate() + 1);
    }

    els.year.innerHTML = '<div class="year-head"><span class="cal-title">'
      + (isFa ? num(nowInCalendar().y) : String(today.y)) + "</span></div>"
      + '<div class="year-grid">' + cells + "</div>"
      + '<div class="year-legend"><span>' + t("Less") + "</span>"
      + '<i class="lv0"></i><i class="lv1"></i><i class="lv2"></i><i class="lv3"></i><i class="lv4"></i>'
      + "<span>" + t("More") + "</span></div>";

    els.year.querySelectorAll(".px").forEach(function (cell) {
      cell.addEventListener("click", function () { openDay(cell.dataset.iso); });
    });
  }

  /* ── Day sheet ──────────────────────────────────────── */

  async function openDay(dayIso) {
    haptic();
    buildSheet();

    els.sheetTitle.textContent = dayIso;
    els.sheetBody.innerHTML = "";
    els.sheet.hidden = false;
    requestAnimationFrame(function () { els.sheet.classList.add("is-open"); });

    var list = monthItems.filter(function (item) { return item.date === dayIso; });
    if (!list.length) {
      try {
        var data = await fetchRange(dayIso, dayIso);
        list = data.items || [];
      } catch (_) { list = []; }
    }

    if (!list.length) {
      els.sheetBody.innerHTML = '<p class="cal-empty">' + t("No events on this day.") + "</p>";
      return;
    }

    list.forEach(function (item) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "day-item cat-" + (item.category || "general");
      row.innerHTML = '<span class="day-item-title"></span><span class="day-item-time"></span>';
      row.querySelector(".day-item-title").textContent = item.title;
      row.querySelector(".day-item-time").textContent =
        item.all_day || !item.time_hm ? t("all day") : num(item.time_hm);

      row.addEventListener("click", function () {
        closeSheet();
        // Straight into the existing detail sheet, so there is exactly one
        // place in the app that knows how to show an event.
        if (window.TMApp && window.TMApp.openDetail) window.TMApp.openDetail(item.id);
      });
      els.sheetBody.appendChild(row);
    });
  }

  function buildSheet() {
    if (els.sheet) return;

    var sheet = document.createElement("div");
    sheet.className = "day-overlay";
    sheet.hidden = true;
    sheet.innerHTML = '<div class="day-dialog" role="dialog" aria-modal="true">'
      + '<div class="day-handle" aria-hidden="true"></div>'
      + '<div class="day-head"><span class="day-title" id="dayTitle"></span>'
      + '<button type="button" class="icon-btn" id="dayClose" aria-label="' + t("Close") + '">✕</button></div>'
      + '<div class="day-body" id="dayBody"></div></div>';
    document.body.appendChild(sheet);
    if (isFa) sheet.setAttribute("dir", "rtl");

    els.sheet = sheet;
    els.sheetTitle = sheet.querySelector("#dayTitle");
    els.sheetBody = sheet.querySelector("#dayBody");
    sheet.querySelector("#dayClose").addEventListener("click", closeSheet);
    sheet.addEventListener("click", function (e) { if (e.target === sheet) closeSheet(); });
  }

  function closeSheet() {
    if (!els.sheet) return;
    els.sheet.classList.remove("is-open");
    setTimeout(function () { els.sheet.hidden = true; }, 200);
  }

  /* ── Tabs ───────────────────────────────────────────── */

  function show(view) {
    haptic("select");
    els.list.hidden = view !== "list";
    els.month.hidden = view !== "month";
    els.year.hidden = view !== "year";

    els.tabs.forEach(function (tab) {
      var active = tab.dataset.view === view;
      tab.classList.toggle("is-active", active);
      if (active) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });

    if (view === "month") renderMonth();
    if (view === "year") renderYear();
  }

  function init() {
    els.list = document.getElementById("eventList");
    els.month = document.getElementById("monthView");
    els.year = document.getElementById("yearView");
    var bar = document.getElementById("tabbar");
    if (!els.list || !els.month || !els.year || !bar || !window.TMApp) return;

    els.tabs = Array.prototype.slice.call(bar.querySelectorAll(".tab"));
    els.tabs.forEach(function (tab) {
      tab.querySelector("span").textContent = t(tab.dataset.view === "list" ? "List"
        : tab.dataset.view === "month" ? "Month" : "Year");
      tab.addEventListener("click", function () { show(tab.dataset.view); });
    });

    cursor = nowInCalendar();
    bar.hidden = false;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
'''

create("static/views.js", VIEWS_JS, "month and year views")

# ══════════════════════════════════════════════════════════════════════
# 6. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13b — month view, year grid, tab bar ──────── */

.view-pane { padding-top: 12px; }

.tabbar {
  position: fixed; inset-inline: 0; bottom: 0; z-index: 40;
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  padding-bottom: env(safe-area-inset-bottom, 0px);
  background: var(--surface);
  border-top: 1px solid var(--border);
}
.tab {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  padding: 9px 0 8px;
  border: none; background: none; cursor: pointer;
  color: var(--text-muted);
  font-family: inherit; font-size: 0.7rem; font-weight: 700;
}
.tab.is-active { color: var(--brand); }
.tab:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; }

/* The list already reserved room for the floating add button; the bar sits
   under it and needs its own. */
.app-main { padding-bottom: calc(172px + env(safe-area-inset-bottom, 0px)); }

.cal-head, .year-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 14px;
}
.cal-title { font-size: 1rem; font-weight: 800; }

.cal-dow {
  display: grid; grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px; margin-bottom: 6px;
}
.cal-dow span {
  text-align: center; font-size: 0.7rem; font-weight: 700;
  color: var(--text-muted);
}

.cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.cal-day {
  aspect-ratio: 1;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px;
  border: none; border-radius: var(--r-md);
  background: var(--surface);
  color: var(--text);
  font-family: inherit; font-size: 0.85rem;
  cursor: pointer;
}
.cal-day:active { background: var(--surface-2); }

/* Today changes shape, not only colour — a tint alone says nothing to anyone
   who cannot separate these two hues, or to anyone outdoors. */
.cal-day.is-today { background: var(--brand); color: #fff; font-weight: 800; }

.cal-dots { display: flex; gap: 2px; height: 5px; }
.cal-dots span {
  width: 4px; height: 4px; border-radius: 50%;
  background: var(--brand);
}
.cal-day.is-today .cal-dots span { background: rgba(255, 255, 255, 0.85); }

.year-grid {
  display: grid; grid-template-rows: repeat(7, 1fr);
  grid-auto-flow: column; grid-auto-columns: 1fr;
  gap: 2px;
  direction: ltr;
}
.px {
  aspect-ratio: 1; min-width: 0;
  border: none; border-radius: 2px; padding: 0;
  cursor: pointer;
}
/* One ramp, five steps, keyed to how many events fall on the day. Category
   colours here would turn 371 squares into confetti with no pattern in it. */
.px.lv0 { background: var(--border); }
.px.lv1 { background: rgba(91, 108, 248, 0.28); }
.px.lv2 { background: rgba(91, 108, 248, 0.50); }
.px.lv3 { background: rgba(91, 108, 248, 0.74); }
.px.lv4 { background: var(--brand); }
.px.is-today { box-shadow: 0 0 0 2px var(--tone-today); }

.year-legend {
  display: flex; align-items: center; gap: 5px;
  margin-top: 14px;
  font-size: 0.72rem; color: var(--text-muted);
}
.year-legend i { width: 10px; height: 10px; border-radius: 2px; }
.year-legend i.lv0 { background: var(--border); }
.year-legend i.lv1 { background: rgba(91, 108, 248, 0.28); }
.year-legend i.lv2 { background: rgba(91, 108, 248, 0.50); }
.year-legend i.lv3 { background: rgba(91, 108, 248, 0.74); }
.year-legend i.lv4 { background: var(--brand); }

.cal-empty, .cal-error {
  margin: 0; padding: 18px 2px;
  font-size: 0.85rem; color: var(--text-muted); text-align: center;
}

.day-overlay {
  position: fixed; inset: 0; z-index: 75;
  display: flex; align-items: flex-end; justify-content: center;
  background: rgba(10, 12, 30, 0.5);
  opacity: 0; transition: opacity 200ms ease;
}
.day-overlay.is-open { opacity: 1; }
.day-dialog {
  width: min(100%, var(--app-max, 560px));
  max-height: 70svh; overflow-y: auto;
  padding: 10px 20px calc(24px + env(safe-area-inset-bottom, 0px));
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl) var(--r-xl) 0 0;
  transform: translateY(16px);
  transition: transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
}
.day-overlay.is-open .day-dialog { transform: translateY(0); }
.day-handle {
  width: 40px; height: 4px; margin: 6px auto 14px;
  border-radius: var(--r-pill); background: var(--border);
}
.day-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 12px;
}
.day-title { font-size: 0.95rem; font-weight: 800; }

.day-item {
  width: 100%;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 14px; margin-bottom: 8px;
  border: 1px solid var(--border);
  border-inline-start: 4px solid var(--cat-general);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text);
  font-family: inherit; text-align: start;
  cursor: pointer;
}
.day-item-title { font-size: 0.88rem; font-weight: 700; }
.day-item-time { font-size: 0.78rem; color: var(--text-muted); flex-shrink: 0; }

@media (prefers-reduced-motion: reduce) {
  .day-overlay, .day-dialog { transition: none; }
}
'''

append("static/style.css", STYLES, "Batch 13b — month view", "calendar styles")

# ══════════════════════════════════════════════════════════════════════
# 7. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from datetime import date

import pytest

from app.utils.occurrences import expand_occurrences


def _event(**overrides) -> dict:
    base = {
        "date_iso": "2026-03-10",
        "tz_name": "Europe/Berlin",
        "all_day": True,
        "repeat": "none",
    }
    base.update(overrides)
    return base


YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


def test_a_one_off_event_appears_once_and_only_in_range() -> None:
    event = _event()

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [date(2026, 3, 10)]
    assert expand_occurrences(event, date(2026, 4, 1), YEAR_END) == []


def test_a_yearly_birthday_draws_one_dot_a_year() -> None:
    """The stored event knows one occurrence; a calendar needs the series."""
    event = _event(date_iso="1998-07-20", repeat="yearly")

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [date(2026, 7, 20)]


def test_a_monthly_event_draws_twelve() -> None:
    found = expand_occurrences(_event(date_iso="2026-01-15", repeat="monthly"),
                               YEAR_START, YEAR_END)

    assert len(found) == 12
    assert found[0] == date(2026, 1, 15)
    assert found[-1] == date(2026, 12, 15)


def test_a_monthly_event_on_the_31st_comes_back_after_a_short_month() -> None:
    """Re-anchoring is advance_occurrence's job; this proves we did not lose it."""
    found = expand_occurrences(_event(date_iso="2026-01-31", repeat="monthly"),
                               date(2026, 1, 1), date(2026, 4, 30))

    assert found == [date(2026, 1, 31), date(2026, 2, 28),
                     date(2026, 3, 31), date(2026, 4, 30)]


def test_a_weekly_event_lands_on_the_same_weekday_every_time() -> None:
    found = expand_occurrences(_event(date_iso="2026-09-07", repeat="weekly"),
                               date(2026, 9, 1), date(2026, 9, 30))

    assert found == [date(2026, 9, 7), date(2026, 9, 14),
                     date(2026, 9, 21), date(2026, 9, 28)]


def test_a_daily_event_that_started_years_ago_still_fills_the_window() -> None:
    """Fast-forwarding must land on the series, not next to it."""
    found = expand_occurrences(_event(date_iso="2019-01-01", repeat="daily"),
                               date(2026, 9, 1), date(2026, 9, 5))

    assert found == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
                     date(2026, 9, 4), date(2026, 9, 5)]


def test_repeat_until_ends_the_series() -> None:
    found = expand_occurrences(
        _event(date_iso="2026-01-15", repeat="monthly", repeat_until="2026-04-30"),
        YEAR_START, YEAR_END,
    )

    assert found == [date(2026, 1, 15), date(2026, 2, 15),
                     date(2026, 3, 15), date(2026, 4, 15)]


def test_the_step_cap_holds() -> None:
    """A daily series over a decade must return, not spin."""
    found = expand_occurrences(_event(date_iso="2026-01-01", repeat="daily"),
                               YEAR_START, date(2036, 1, 1), max_steps=10)

    assert len(found) == 10


@pytest.mark.parametrize("bad", [{}, {"date_iso": ""}, {"date_iso": "not-a-date"}])
def test_a_broken_event_yields_nothing_instead_of_raising(bad) -> None:
    """One malformed document must not take the whole calendar down."""
    assert expand_occurrences(bad, YEAR_START, YEAR_END) == []


def test_an_inverted_range_is_empty() -> None:
    assert expand_occurrences(_event(), YEAR_END, YEAR_START) == []
'''

create("tests/test_occurrences.py", TESTS, "occurrence tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13b (month view, year grid)\n")
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
    print("      git add -A && git commit -m 'Batch 13b: month view and year grid'")
    print()
    print("  Open the month and year tabs on a real phone: the grid maths is")
    print("  tested, the way it feels under a thumb is not.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
