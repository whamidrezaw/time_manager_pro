/* ──────────────────────────────────────────────────────────────
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
