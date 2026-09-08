#!/usr/bin/env python3
"""
apply_batch13c.py — TimeManager Pro, Batch 13c

  * a finished repeating series stops drawing occurrences it will never have
  * the add-event button comes back out from behind the tab bar
  * the year grid moves into the month view as a strip; the year tab is gone
  * a "today" button in the month header
  * the month view can no longer paint a stale month's data over a new one

Run once from the repository root:

    python apply_batch13c.py --check   # dry run, writes nothing
    python apply_batch13c.py           # apply

Requires Batch 13b. Safe to run twice.
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
              "— is Batch 13b applied?")
        return

    PENDING[path] = text.replace(old, new, 1)
    _note(" OK ", f"{rel}: {label}")


def remove(rel: str, old: str, label: str) -> None:
    """Delete a block. Absence is the "already applied" signal here, which is
    why this cannot reuse patch()'s marker logic."""
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found")
        return

    count = text.count(old)
    if count == 0:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return
    if count > 1:
        _fail(f"{rel}: block for '{label}' matched {count} times, expected 1")
        return

    PENDING[path] = text.replace(old, "", 1)
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
# 1. A finished series must stop
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/utils/occurrences.py",
    old="""    until = event.get("repeat_until")
    anchor_day = int(date_iso[8:10] or 1)
""",
    new="""    until = event.get("repeat_until")
    anchor_day = int(date_iso[8:10] or 1)

    # A repeat rule on its own never ends, so replaying it forward invents
    # occurrences the event will never actually have. The worker already
    # decided when the series was over — it wrote notify_status "done" and
    # left event_ts_utc on the last real occurrence — so the calendar stops
    # exactly where the reminders did instead of drawing dots into eternity.
    if str(event.get("notify_status")) == "done":
        last = event.get("event_ts_utc")
        if isinstance(last, datetime):
            end = min(end, last.astimezone(tz).date())
            if end < start:
                return []
""",
    marker='notify_status")) == "done"',
    label="stop a finished series",
)

patch(
    "app/routes/calendar.py",
    old="""PROJECTION = {
    "title": 1, "date_iso": 1, "tz_name": 1, "all_day": 1, "time_hm": 1,
    "repeat": 1, "repeat_until": 1, "category": 1, "pinned": 1,
}
""",
    new="""PROJECTION = {
    "title": 1, "date_iso": 1, "tz_name": 1, "all_day": 1, "time_hm": 1,
    "repeat": 1, "repeat_until": 1, "category": 1, "pinned": 1,
    # Both are what tells expand_occurrences that a series has already ended.
    "notify_status": 1, "event_ts_utc": 1,
}
""",
    marker='"notify_status": 1, "event_ts_utc": 1,',
    label="project the fields that mark a finished series",
)

patch(
    "tests/test_occurrences.py",
    old="""def test_an_inverted_range_is_empty() -> None:
    assert expand_occurrences(_event(), YEAR_END, YEAR_START) == []
""",
    new="""def test_an_inverted_range_is_empty() -> None:
    assert expand_occurrences(_event(), YEAR_END, YEAR_START) == []


def test_a_finished_series_stops_at_its_last_occurrence() -> None:
    \"\"\"The reported bug: a weekly event kept drawing dots after it was over.

    A repeat rule has no end of its own, so replaying it forward invents
    occurrences the event will never have. notify_status "done" plus the
    stored event_ts_utc is where the worker left the series.
    \"\"\"
    event = _event(
        date_iso="2026-01-05",
        repeat="weekly",
        notify_status="done",
        event_ts_utc=datetime(2026, 2, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [
        date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19),
        date(2026, 1, 26), date(2026, 2, 2),
    ]


def test_a_running_series_is_untouched_by_that_rule() -> None:
    event = _event(
        date_iso="2026-01-05",
        repeat="weekly",
        notify_status="pending",
        event_ts_utc=datetime(2026, 2, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert len(expand_occurrences(event, YEAR_START, YEAR_END)) > 5


def test_a_finished_series_that_ended_before_the_window_draws_nothing() -> None:
    event = _event(
        date_iso="2024-01-05",
        repeat="weekly",
        notify_status="done",
        event_ts_utc=datetime(2024, 3, 1, 8, 0, tzinfo=timezone.utc),
    )

    assert expand_occurrences(event, YEAR_START, YEAR_END) == []
""",
    marker="def test_a_finished_series_stops_at_its_last_occurrence",
    label="tests for the finished series",
)

patch(
    "tests/test_occurrences.py",
    old="""from datetime import date
""",
    new="""from datetime import date, datetime, timezone
""",
    marker="from datetime import date, datetime, timezone",
    label="test imports",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Two tabs, not three
# ══════════════════════════════════════════════════════════════════════

remove(
    "templates/index.html",
    old="""  <main id="yearView" class="app-main view-pane" hidden></main>\n""",
    label="drop the year pane",
)

remove(
    "templates/index.html",
    old="""    <button type="button" class="tab" data-view="year">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      <span data-i18n>Year</span>
    </button>
""",
    label="drop the year tab",
)

# ══════════════════════════════════════════════════════════════════════
# 3. The views, rewritten
# ══════════════════════════════════════════════════════════════════════

VIEWS_JS = r'''/* ──────────────────────────────────────────────────────────────
   views.js — the month view, with the year strip above it.

   Standalone like referral.js and share.js. Everything it needs from the
   main closure comes through window.TMApp; if that is missing the tab bar
   never appears and the list keeps working on its own.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "List": "لیست", "Month": "ماه", "Today": "امروز",
    "No events on this day.": "این روز رویدادی ندارد.",
    "Close": "بستن",
    "Could not load the calendar.": "تقویم بارگذاری نشد.",
    "all day": "تمام‌روز",
    "Previous month": "ماه قبل", "Next month": "ماه بعد",
    "Go to today": "برو به امروز",
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

  function isoOf(dateObj) {
    return iso(dateObj.getFullYear(), dateObj.getMonth() + 1, dateObj.getDate());
  }

  /* ── Calendar maths ─────────────────────────────────
     Persian users get a Jalali year, everyone else a Gregorian one. The
     conversion is borrowed from app.js rather than written again — two files
     disagreeing about what day it is would be a very hard bug to see. */

  var J = (window.TMApp && window.TMApp.jalali) || null;
  var useJalali = isFa && !!J;

  function gregorianDaysInMonth(y, m) { return new Date(y, m, 0).getDate(); }

  function monthMeta(y, m) {
    if (useJalali) {
      var first = J.toGregorian(y, m, 1);
      var start = new Date(first.gy, first.gm - 1, first.gd);
      return {
        label: JALALI_MONTHS[m - 1] + " " + num(y),
        days: J.daysInMonth(y, m),
        offset: (start.getDay() + 1) % 7,          // the Persian week starts on Saturday
        toIso: function (day) {
          var g = J.toGregorian(y, m, day);
          return iso(g.gy, g.gm, g.gd);
        },
      };
    }
    return {
      label: GREGORIAN_MONTHS[m - 1] + " " + y,
      days: gregorianDaysInMonth(y, m),
      offset: (new Date(y, m - 1, 1).getDay() + 6) % 7,
      toIso: function (day) { return iso(y, m, day); },
    };
  }

  function todayParts() {
    var now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
  }

  function nowInCalendar() {
    var g = todayParts();
    if (!useJalali) return g;
    var j = J.fromGregorian(g.y, g.m, g.d);
    return { y: j.jy, m: j.jm, d: j.jd };
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

  /* ── State ──────────────────────────────────────────── */

  var els = {};
  var cursor = null;
  var monthItems = [];
  // Every render takes a ticket. A response that comes back holding an old
  // one is dropped, which is what stops a slow request for last month from
  // painting itself over the month now on screen — and from leaving its
  // events behind for the day sheet to find.
  var renderToken = 0;

  /* ── Year strip ─────────────────────────────────────── */

  function yearBounds() {
    var today = nowInCalendar();
    var first = useJalali ? J.toGregorian(today.y, 1, 1) : { gy: today.y, gm: 1, gd: 1 };
    var after = useJalali ? J.toGregorian(today.y + 1, 1, 1) : { gy: today.y + 1, gm: 1, gd: 1 };
    var start = new Date(first.gy, first.gm - 1, first.gd);
    var next = new Date(after.gy, after.gm - 1, after.gd);
    // Measured, not assumed: both calendars have 365- and 366-day years, and
    // one square too many is a square belonging to next year.
    return { start: start, days: Math.round((next - start) / 86400000) };
  }

  async function renderStrip() {
    var bounds = yearBounds();
    var last = new Date(bounds.start);
    last.setDate(last.getDate() + bounds.days - 1);

    var counts = {};
    try {
      var data = await fetchRange(isoOf(bounds.start), isoOf(last));
      counts = data.days || {};
    } catch (_) {
      return;
    }

    var todayIso = isoOf(new Date());
    var cells = "";
    var walk = new Date(bounds.start);
    for (var i = 0; i < bounds.days; i++) {
      var key = isoOf(walk);
      cells += '<button type="button" class="px lv' + Math.min(counts[key] || 0, 4)
        + (key === todayIso ? " is-today" : "") + '" data-iso="' + key
        + '" aria-label="' + key + '"></button>';
      walk.setDate(walk.getDate() + 1);
    }

    els.strip.innerHTML = cells;
    els.strip.querySelectorAll(".px").forEach(function (cell) {
      cell.addEventListener("click", function () { openDay(cell.dataset.iso); });
    });
  }

  /* ── Month grid ─────────────────────────────────────── */

  function renderMonth() {
    var meta = monthMeta(cursor.y, cursor.m);
    var today = nowInCalendar();
    var onToday = cursor.y === today.y && cursor.m === today.m;
    var dow = useJalali ? JALALI_DOW : GREGORIAN_DOW;

    var html = '<div class="year-strip" id="yearStrip" aria-hidden="true"></div>'
      + '<div class="cal-head">'
      + '<button type="button" class="icon-btn" data-step="-1" aria-label="' + t("Previous month") + '">‹</button>'
      + '<span class="cal-head-mid"><span class="cal-title">' + meta.label + "</span>"
      + '<button type="button" class="cal-today" id="calToday" aria-label="' + t("Go to today") + '"'
      + (onToday ? " hidden" : "") + ">" + t("Today") + "</button></span>"
      + '<button type="button" class="icon-btn" data-step="1" aria-label="' + t("Next month") + '">›</button>'
      + '</div><div class="cal-dow">';
    dow.forEach(function (name) { html += "<span>" + name + "</span>"; });
    html += '</div><div class="cal-grid">';

    for (var i = 0; i < meta.offset; i++) html += "<span></span>";
    for (var day = 1; day <= meta.days; day++) {
      var dayIso = meta.toIso(day);
      var isToday = onToday && day === today.d;
      html += '<button type="button" class="cal-day' + (isToday ? " is-today" : "")
        + '" data-iso="' + dayIso + '"><span class="cal-num">' + num(day)
        + '</span><span class="cal-dots" data-dots="' + dayIso + '"></span></button>';
    }
    html += "</div>";

    els.month.innerHTML = html;
    els.strip = els.month.querySelector("#yearStrip");

    els.month.querySelectorAll("[data-step]").forEach(function (button) {
      button.addEventListener("click", function () {
        haptic("select");
        cursor = stepMonth(cursor.y, cursor.m, Number(button.dataset.step));
        renderMonth();
      });
    });
    var todayBtn = els.month.querySelector("#calToday");
    if (todayBtn) {
      todayBtn.addEventListener("click", function () {
        haptic("select");
        cursor = nowInCalendar();
        renderMonth();
      });
    }
    els.month.querySelectorAll(".cal-day").forEach(function (button) {
      button.addEventListener("click", function () { openDay(button.dataset.iso); });
    });

    renderToken += 1;
    paintMonth(meta, renderToken);
    renderStrip();
  }

  async function paintMonth(meta, token) {
    var start = meta.toIso(1);
    var end = meta.toIso(meta.days);

    var data;
    try {
      data = await fetchRange(start, end);
    } catch (_) {
      return;                                  // a calendar without dots is still a calendar
    }
    if (token !== renderToken) return;          // a newer month is on screen

    monthItems = data.items || [];
    Object.keys(data.days || {}).forEach(function (key) {
      var slot = els.month.querySelector('[data-dots="' + key + '"]');
      if (!slot) return;
      var dots = "";
      for (var i = 0; i < Math.min(data.days[key], 3); i++) dots += "<span></span>";
      slot.innerHTML = dots;
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
        // Straight into the existing detail view: exactly one place in the app
        // knows how to show an event.
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
    document.body.classList.toggle("on-calendar", view === "month");

    els.tabs.forEach(function (tab) {
      var active = tab.dataset.view === view;
      tab.classList.toggle("is-active", active);
      if (active) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });

    if (view === "month") renderMonth();
  }

  function init() {
    els.list = document.getElementById("eventList");
    els.month = document.getElementById("monthView");
    var bar = document.getElementById("tabbar");
    if (!els.list || !els.month || !bar || !window.TMApp) return;

    els.tabs = Array.prototype.slice.call(bar.querySelectorAll(".tab"));
    els.tabs.forEach(function (tab) {
      tab.querySelector("span").textContent = t(tab.dataset.view === "list" ? "List" : "Month");
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

create("static/views.js", VIEWS_JS, "month view with the year strip")

# ══════════════════════════════════════════════════════════════════════
# 4. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13c — tab bar, year strip, today button ───── */

/* The bar was sitting on z-index 40, above the add button on 35, and swallowed
   it. It belongs under the button and well under the sheets, which start at 40. */
.tabbar { z-index: 30; grid-template-columns: repeat(2, minmax(0, 1fr)); }

/* ...and the button has to clear the bar rather than sit behind it. */
.floating-add-btn { bottom: calc(var(--safe-bottom) + 62px); }

.cal-head-mid { display: inline-flex; align-items: center; gap: 8px; }
.cal-today {
  padding: 3px 12px;
  border: 1px solid var(--border-strong, var(--border));
  border-radius: var(--r-pill);
  background: none;
  color: var(--text-2);
  font-family: inherit; font-size: 0.72rem; font-weight: 700;
  cursor: pointer;
}
.cal-today:active { background: var(--surface-2); }

/* The year grid, demoted from its own tab to a strip. It costs about forty
   pixels here and still shows the shape of the whole year at a glance. */
.year-strip {
  display: grid; grid-template-rows: repeat(7, 1fr);
  grid-auto-flow: column; grid-auto-columns: 1fr;
  gap: 1.5px;
  direction: ltr;
  margin-bottom: 18px;
}
.year-strip .px { border-radius: 1.5px; }
.year-strip .px.is-today { box-shadow: 0 0 0 1.5px var(--tone-today); }

.year-legend { display: none; }
'''

append("static/style.css", STYLES, "Batch 13c — tab bar", "layout fixes")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13c (finished series, layout, year strip)\n")
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
    print("      git add -A && git commit -m 'Batch 13c: finished series, tab bar, year strip'")
    print()
    print("  The event detail page is not in this batch — it comes next, on its own,")
    print("  because rebuilding it touches a different part of the app entirely.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
