/* ──────────────────────────────────────────────────────────────
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
