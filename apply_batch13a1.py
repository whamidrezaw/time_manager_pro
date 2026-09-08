#!/usr/bin/env python3
"""
apply_batch13a1.py — TimeManager Pro, Batch 13a-1

  * date picker opens on today instead of 1900
  * the event list is reconciled by id instead of rebuilt from scratch
  * FLIP reordering and entry animations
  * section headers: Pinned / Today / Tomorrow / Later
  * a real "tomorrow" tone, louder today and tomorrow cards
  * SVG icons on the card instead of emoji, plus the reminder time
  * haptics

Run once from the repository root:

    python apply_batch13a1.py --check   # dry run, writes nothing
    python apply_batch13a1.py           # apply

Safe to run twice. Nothing is written unless every step resolves.
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
        _fail(f"{rel}: anchor for '{label}' matched {count} times, expected 1")
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


def flush() -> None:
    for path, content in PENDING.items():
        path.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 1. The date picker opened on 1900
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    const index = Math.max(0, values.indexOf(selected));
    el.scrollTop = index * DP_ITEM_H;
  }
""",
    new="""    dpScrollTo(el, Math.max(0, values.indexOf(selected)));
  }

  function dpScrollTo(el, index) {
    const top = index * DP_ITEM_H;
    el.scrollTop = top;

    // scroll-snap-type is mandatory on these columns, and the snap re-runs on
    // the layout that follows a rebuild — often dragging the column back to
    // the nearest snap point. Re-asserting on the next frame makes the
    // intended item stick.
    requestAnimationFrame(() => {
      if (Math.abs(el.scrollTop - top) > 1) el.scrollTop = top;
    });
  }
""",
    marker="function dpScrollTo",
    label="wheel scroll survives the snap",
)

patch(
    "static/app.js",
    old="""    dpRender();
    state.lastFocusedElement = document.activeElement;
    if (els.dpOverlay) {
      els.dpOverlay.hidden = false;
      els.dpOverlay.setAttribute("aria-hidden", "false");
    }
""",
    new="""    state.lastFocusedElement = document.activeElement;

    // Order matters here, and getting it wrong is what made the picker open on
    // 1900 / January / 1. While the overlay is hidden the wheels have no
    // scroll box, so the scrollTop that centres today was silently dropped and
    // every column stayed parked on its first item. Show first, fill second.
    if (els.dpOverlay) {
      els.dpOverlay.hidden = false;
      els.dpOverlay.setAttribute("aria-hidden", "false");
    }
    dpRender();
""",
    marker="Show first, fill second",
    label="show the overlay before filling the wheels",
)

# ══════════════════════════════════════════════════════════════════════
# 2. A real "tomorrow" tone
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    let tone = "long";
    if      (diff.totalDays <= 3)   tone = "critical";
    else if (diff.totalDays <= 7)   tone = "critical";
""",
    new="""    let tone = "long";
    // Tomorrow is its own tone rather than the top of the critical bucket: the
    // list groups by it and it has to read louder than "in a week". The old
    // <=3 and <=7 branches both returned "critical", so the second could never
    // be reached — they are one branch now.
    if      (diff.totalDays === 1)  tone = "tomorrow";
    else if (diff.totalDays <= 7)   tone = "critical";
""",
    marker='tone = "tomorrow"',
    label="tomorrow tone",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Stop wiping the list before every request
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""      state.skip = 0;
      setSkeleton(true);
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
""",
    new="""      state.skip = 0;

      // The skeleton only stands in for an empty list. Clearing the cards
      // before the response arrives threw away the very nodes the reconciler
      // animates from, and made every filter change flash empty on the way.
      setSkeleton(!els.eventsWrap || !els.eventsWrap.children.length);
""",
    marker="The skeleton only stands in for an empty list",
    label="keep the old cards while reloading",
)

patch(
    "static/app.js",
    old="""      state.filteredEvents = [];
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
""",
    new="""      state.filteredEvents = [];
      clearCards();
""",
    marker="clearCards();\n      if (els.listState) els.listState.hidden = true;",
    label="clear through the card index on error",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The renderer
# ══════════════════════════════════════════════════════════════════════

OLD_RENDER_BLOCK = '  function renderEvents() {\n    if (!els.eventsWrap) return;\n\n    if (!state.filteredEvents.length) {\n      els.eventsWrap.innerHTML = "";\n      showStatePanel();\n      return;\n    }\n\n    const frag = document.createDocumentFragment();\n\n    state.filteredEvents.forEach((event) => {\n      const cd = getCountdownData(event.next_date_iso || event.date_iso);\n      const catClass = `cat-${event.category || "general"}`;\n      const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";\n      const repeatLabel = REPEAT_LABELS[event.repeat] || "One time";\n\n      const art = document.createElement("article");\n      art.className = `event-card ${catClass}`;\n      art.tabIndex = 0;\n      art.setAttribute("role", "button");\n      art.setAttribute("aria-label", `Open details for ${event.title}`);\n      art.dataset.id = event.id;\n\n      // Progress bar: how close to event (cap at 365 days)\n      const progressPct = cd.totalDays <= 0\n        ? 100\n        : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));\n\n      art.innerHTML = `\n        <div class="event-card-top">\n          <div class="event-head">\n            <h3 class="event-title">${escapeHtml(event.title)}</h3>\n            <div class="event-badges">\n              ${event.pinned ? \'<span class="badge badge-pin">📌 Pinned</span>\' : ""}\n              <span class="badge ${getCatBadgeClass(event.category)}">${escapeHtml(catLabel)}</span>\n              <span class="urgency-badge urgency-${cd.tone}">${escapeHtml(cd.shortText)}</span>\n            </div>\n          </div>\n          <span class="event-repeat">${escapeHtml(repeatLabel)}</span>\n        </div>\n\n        <div class="event-progress-wrap">\n          <div class="event-progress-bar">\n            <div class="event-progress-fill" style="width:${progressPct}%"></div>\n          </div>\n          <span class="event-progress-label">${\n            cd.totalDays <= 0 ? "Today!" :\n            cd.totalDays === 0 ? "Today!" :\n            `${cd.totalDays}d`\n          }</span>\n        </div>\n\n        <div class="event-dates">\n          <span>📅 ${escapeHtml(event.next_date_iso || event.date_iso || "—")}${\n            event.all_day === false && event.time_hm\n              ? ` · ${escapeHtml(event.time_hm)}`\n              : ""\n          }</span>\n          <span class="event-dates-sep">•</span>\n          <span>🗓️ ${escapeHtml(event.next_date_jalali || event.date_jalali || "—")}</span>\n        </div>\n\n        <div class="event-bottom">\n          <span class="status-dot status-${escapeHtml(event.notify_status || "pending")}"></span>\n          <span>${escapeHtml(t(STATUS_LABELS[event.notify_status] || "Pending"))}</span>\n        </div>\n      `;\n\n      art.addEventListener("click",   () => openDetail(event.id));\n      art.addEventListener("keydown", (e) => {\n        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(event.id); }\n      });\n\n      frag.appendChild(art);\n    });\n\n    els.eventsWrap.innerHTML = "";\n    els.eventsWrap.appendChild(frag);\n    showStatePanel();\n  }\n\n'

NEW_RENDER = r'''  // Reconciled against the DOM by id rather than rebuilt. The old version
  // emptied the wrapper and recreated every article on each render, which
  // dropped keyboard focus, re-bound every listener, and — worst of all — left
  // the browser with no way to know that a card had moved rather than been
  // replaced. Nothing could be animated because nothing survived.

  const cardIndex = new Map();      // event id -> <article>
  const headerIndex = new Map();    // section key -> <div>

  const SECTION_LABELS = {
    pinned: "Pinned",
    today: "Today",
    tomorrow: "Tomorrow",
    later: "Later",
  };

  const CARD_ICONS = {
    pin: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    moon: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    bell: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
  };

  function prefersReducedMotion() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }

  function clearCards() {
    if (els.eventsWrap) els.eventsWrap.replaceChildren();
    cardIndex.clear();
    headerIndex.clear();
  }

  // Pinned events are sorted first by the server, so that block is always
  // contiguous and a single header can cover it. Checking pinned before the
  // day is what keeps the two schemes from fighting.
  function sectionFor(event, cd) {
    if (event.pinned) return "pinned";
    if (cd.totalDays === 0) return "today";
    if (cd.totalDays === 1) return "tomorrow";
    return "later";
  }

  function sectionHeader(key) {
    let node = headerIndex.get(key);
    if (node) return node;

    node = document.createElement("div");
    node.className = `event-section section-${key}`;
    node.setAttribute("role", "presentation");
    node.innerHTML = '<span class="event-section-label"></span>';
    node.firstChild.textContent = t(SECTION_LABELS[key] || key);
    headerIndex.set(key, node);
    return node;
  }

  // Mirrors the logic openEditComposer uses for the reminder field, so the
  // time on the card and the time in the form can never disagree.
  function reminderTimeText(event) {
    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (spec && spec.mode === "relative") return null;
    const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9));
    const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0));
    return `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
  }

  function buildCard(id) {
    const art = document.createElement("article");
    art.className = "event-card";
    art.tabIndex = 0;
    art.setAttribute("role", "button");
    art.dataset.id = id;
    art.innerHTML = `
      <div class="event-card-top">
        <div class="event-head">
          <h3 class="event-title" data-f="title"></h3>
          <div class="event-badges">
            <span class="badge badge-pin" data-f="pin" hidden>${CARD_ICONS.pin}<span data-f="pinText"></span></span>
            <span class="badge" data-f="cat"></span>
            <span class="urgency-badge" data-f="urgency"></span>
          </div>
        </div>
        <span class="event-repeat" data-f="repeat"></span>
      </div>

      <div class="event-progress-wrap">
        <div class="event-progress-bar">
          <div class="event-progress-fill" data-f="fill"></div>
        </div>
        <span class="event-progress-label" data-f="progress"></span>
      </div>

      <div class="event-dates">
        <span class="event-date-item">${CARD_ICONS.calendar}<span data-f="iso"></span></span>
        <span class="event-dates-sep">•</span>
        <span class="event-date-item">${CARD_ICONS.moon}<span data-f="jalali"></span></span>
        <span class="event-date-item event-reminder" data-f="reminderWrap" hidden>${CARD_ICONS.bell}<span data-f="reminder"></span></span>
      </div>

      <div class="event-bottom">
        <span class="status-dot" data-f="dot"></span>
        <span data-f="status"></span>
      </div>
    `;

    const fields = {};
    art.querySelectorAll("[data-f]").forEach((el) => { fields[el.dataset.f] = el; });
    art._f = fields;

    // Reading the id off the element at call time rather than closing over it
    // means a reused node can never open the wrong event.
    const open = () => openDetail(art.dataset.id);
    art.addEventListener("click", open);
    art.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });

    return art;
  }

  function fillCard(art, event, cd) {
    const f = art._f;
    const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";

    art.className = `event-card cat-${event.category || "general"} tone-${cd.tone}`;
    art.setAttribute("aria-label", t("Open details for {title}", { title: event.title }));

    f.title.textContent = event.title || "";
    f.pin.hidden = !event.pinned;
    f.pinText.textContent = t("Pinned");
    f.cat.className = `badge ${getCatBadgeClass(event.category)}`;
    f.cat.textContent = catLabel;
    f.urgency.className = `urgency-badge urgency-${cd.tone}`;
    f.urgency.textContent = cd.shortText;
    f.repeat.textContent = t(REPEAT_LABELS[event.repeat] || "One time");

    const pct = cd.totalDays <= 0
      ? 100
      : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));
    f.fill.style.width = `${pct}%`;
    f.progress.textContent = cd.totalDays <= 0 ? t("Today!") : `${cd.totalDays}d`;

    const iso = event.next_date_iso || event.date_iso || "—";
    f.iso.textContent = event.all_day === false && event.time_hm
      ? `${iso} · ${event.time_hm}`
      : iso;
    f.jalali.textContent = event.next_date_jalali || event.date_jalali || "—";

    const reminder = reminderTimeText(event);
    f.reminderWrap.hidden = !reminder;
    if (reminder) f.reminder.textContent = reminder;

    const status = event.notify_status || "pending";
    f.dot.className = `status-dot status-${status}`;
    f.status.textContent = t(STATUS_LABELS[status] || "Pending");
  }

  function renderEvents() {
    if (!els.eventsWrap) return;

    const wrap = els.eventsWrap;
    if (!state.filteredEvents.length) {
      clearCards();
      showStatePanel();
      return;
    }

    const animate = !prefersReducedMotion();
    const before = animate ? snapshotTops(wrap) : null;

    const desired = [];
    const live = new Set();
    const fresh = [];
    let section = null;

    state.filteredEvents.forEach((event) => {
      const cd = getCountdownData(event.next_date_iso || event.date_iso);
      const key = sectionFor(event, cd);
      if (key !== section) {
        section = key;
        desired.push(sectionHeader(key));
      }

      let art = cardIndex.get(event.id);
      if (!art) {
        art = buildCard(event.id);
        cardIndex.set(event.id, art);
        fresh.push(art);
      }
      fillCard(art, event, cd);
      live.add(event.id);
      desired.push(art);
    });

    cardIndex.forEach((art, id) => {
      if (!live.has(id)) {
        art.remove();
        cardIndex.delete(id);
      }
    });

    const wanted = new Set(desired);
    Array.from(wrap.children).forEach((node) => {
      if (!wanted.has(node)) node.remove();
    });
    desired.forEach((node, i) => {
      if (wrap.children[i] !== node) wrap.insertBefore(node, wrap.children[i] || null);
    });

    if (animate) {
      flipFrom(before, wrap);
      playEntries(fresh);
    }
    showStatePanel();
  }

  /* ── Movement ────────────────────────────────────────
     FLIP: measure where everything was, let the reordering happen, measure
     again, then animate the difference away. Only the vertical offset matters
     in a single column, so one number per node is enough. */

  function snapshotTops(wrap) {
    const tops = new Map();
    Array.from(wrap.children).forEach((node) => {
      tops.set(node, node.getBoundingClientRect().top);
    });
    return tops;
  }

  function flipFrom(before, wrap) {
    if (!before) return;
    Array.from(wrap.children).forEach((node) => {
      const was = before.get(node);
      if (was === undefined) return;                 // new — gets the entry animation
      const delta = was - node.getBoundingClientRect().top;
      if (Math.abs(delta) < 1) return;
      node.animate(
        [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
        { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );
    });
  }

  function playEntries(nodes) {
    nodes.forEach((node, i) => {
      node.animate(
        [
          { opacity: 0, transform: "translateY(10px) scale(0.98)" },
          { opacity: 1, transform: "none" },
        ],
        {
          duration: 260,
          delay: Math.min(i * 40, 240),
          easing: "cubic-bezier(0.22, 1, 0.36, 1)",
          fill: "backwards",
        }
      );
    });
  }

'''

patch(
    "static/app.js",
    old=OLD_RENDER_BLOCK,
    new=NEW_RENDER,
    marker="const cardIndex = new Map();",
    label="incremental renderer",
)

# ══════════════════════════════════════════════════════════════════════
# 5. Haptics on pin
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""  async function toggleCurrentPin() {
""",
    new="""  async function toggleCurrentPin() {
    try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch (_) {}
""",
    marker='HapticFeedback?.impactOccurred?.("light"); } catch (_) {}\n    const',
    label="haptic on pin",
)

# ══════════════════════════════════════════════════════════════════════
# 6. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13a-1 — section headers, urgency, card icons ──
   The list is now reconciled by id, so these elements persist across renders
   and the FLIP animation in app.js has something stable to move. */

.event-section {
  position: sticky; top: 64px; z-index: 5;
  display: flex; align-items: center; gap: 10px;
  margin: 6px 0 -2px;
  padding: 6px 2px;
  background: linear-gradient(var(--bg) 60%, transparent);
}
.event-section::after {
  content: ''; flex: 1; height: 1px;
  background: var(--border);
}
.event-section-label {
  font-size: 0.78rem; font-weight: 800;
  letter-spacing: 0.02em; text-transform: uppercase;
  color: var(--text-muted);
}
.section-pinned   .event-section-label { color: #be185d; }
.section-today    .event-section-label { color: var(--tone-today); }
.section-tomorrow .event-section-label { color: var(--tone-critical); }

/* Tomorrow sits between today and the critical bucket: same family as
   critical, turned up, so the two never read as the same distance away. */
.urgency-tomorrow { background: rgba(234,88,12,0.22); color: var(--tone-critical); }

/* Today and tomorrow have to shout without turning the list into a wall of
   colour, so the shouting is done by the rail and a tint rather than by
   flooding the card. */
.event-card.tone-today,
.event-card.tone-tomorrow {
  border-inline-start-width: 8px;
  box-shadow: var(--shadow-card), 0 6px 20px rgba(190, 24, 93, 0.14);
}
.event-card.tone-today {
  border-inline-start-color: var(--tone-today);
  background:
    linear-gradient(100deg, rgba(190,24,93,0.09), rgba(190,24,93,0) 60%),
    var(--surface);
}
.event-card.tone-tomorrow {
  border-inline-start-color: var(--tone-critical);
  background:
    linear-gradient(100deg, rgba(234,88,12,0.07), rgba(234,88,12,0) 60%),
    var(--surface);
}
.event-card.tone-today .event-title { font-weight: 800; }
.event-card.tone-today .urgency-badge { animation: urgency-pulse 2.4s ease-in-out infinite; }

@keyframes urgency-pulse {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.06); }
}

/* Icons are inline SVG now. Emoji rendered differently on every platform,
   could not take the surrounding colour, and screen readers read their names
   out loud in the middle of a date. */
.event-date-item { display: inline-flex; align-items: center; gap: 5px; }
.event-date-item svg { flex-shrink: 0; opacity: 0.75; }
.event-reminder { color: var(--text-muted); }
.badge-pin { display: inline-flex; align-items: center; gap: 4px; }

@media (prefers-reduced-motion: reduce) {
  .event-card.tone-today .urgency-badge { animation: none; }
}
'''

append("static/style.css", STYLES, "Batch 13a-1 — section headers", "list styles")

# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13a-1 (render engine, sections, picker fix)\n")
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
    print("      git add -A && git commit -m 'Batch 13a-1: incremental rendering and date picker fix'")
    print()
    print("  This batch is front-end only — the Python suite proves nothing about")
    print("  it. Open the Mini App and check the list and the date picker by hand.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
