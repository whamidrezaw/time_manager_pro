#!/usr/bin/env python3
"""
apply_batch13d.py — TimeManager Pro, Batch 13d

The event detail view stops being a bottom sheet and becomes a fixed page:
nothing slides, nothing drags, and the page itself never scrolls. Only the
note scrolls, inside its own box, so a long note can still be read and edited.

Run once from the repository root:

    python apply_batch13d.py --check   # dry run, writes nothing
    python apply_batch13d.py           # apply

Requires Batch 13c. Safe to run twice.
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
              "— is Batch 13c applied?")
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
# 1. A page header instead of a sheet header
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""  <section id="detailSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="detailTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="detailTitle" class="sheet-title">Event Details</h2>
        <p id="detailSubtitle" class="sheet-subtitle">Full view and actions</p>
      </div>
      <button type="button" id="closeDetailX" class="icon-btn" aria-label="Close details">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>
""",
    new="""  <section id="detailSheet" class="sheet detail-page" role="dialog" aria-modal="true" aria-labelledby="detailTitle" aria-hidden="true" hidden>
    <div class="sheet-head">
      <button type="button" id="closeDetailX" class="icon-btn detail-back" aria-label="Close details">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>
      </button>
      <h2 id="detailTitle" class="sheet-title">Event Details</h2>
    </div>
""",
    marker="detail-page",
    label="page header with a back button",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Six facts instead of four
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""      <div class="detail-meta-grid">
        <div class="detail-meta-box">
          <span class="detail-meta-label">📅 Gregorian</span>
          <strong id="detailDateIso">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🗓️ Jalali</span>
          <strong id="detailDateJalali">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🌍 Timezone</span>
          <strong id="detailTimezone">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🔔 Status</span>
          <strong id="detailStatus">—</strong>
        </div>
      </div>
""",
    new="""      <div class="detail-meta-grid">
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Gregorian</span>
          <strong id="detailDateIso">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Jalali</span>
          <strong id="detailDateJalali">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Time</span>
          <strong id="detailTime">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Reminder</span>
          <strong id="detailReminder">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Timezone</span>
          <strong id="detailTimezone">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label" data-i18n>Status</span>
          <strong id="detailStatus">—</strong>
        </div>
      </div>
""",
    marker='id="detailReminder"',
    label="time and reminder on the page",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Filling them, and not grabbing the keyboard on open
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
""",
    new="""    // Queried here rather than added to els: two more entries in that map for
    // two labels is not worth the churn, and the page renders once per open.
    const timeBox = document.getElementById("detailTime");
    if (timeBox) {
      timeBox.textContent = ev.all_day === false && ev.time_hm ? ev.time_hm : t("All day");
    }
    const reminderBox = document.getElementById("detailReminder");
    if (reminderBox) {
      reminderBox.textContent = reminderTimeText(ev) || t("Before the event");
    }

    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
""",
    marker='document.getElementById("detailReminder")',
    label="fill time and reminder",
)

patch(
    "static/app.js",
    old="""    openSheet("detailSheet", els.detailNote);
""",
    new="""    // No focus target any more. On a full page, focusing the note threw the
    // keyboard up over the event the moment it opened.
    openSheet("detailSheet");
""",
    marker="No focus target any more",
    label="stop the keyboard opening with the page",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The layout
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13d — the detail view as a fixed page ─────── */

.detail-page {
  inset: 0;
  width: 100%;
  max-height: none;
  margin-inline: 0;
  padding: 0;
  border-radius: 0;
  box-shadow: none;

  /* The page itself never scrolls. Everything above the note is fixed, and
     the note takes whatever is left — which is what makes a long note
     readable without the whole screen sliding under your thumb. */
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.detail-page .sheet-head {
  flex-shrink: 0;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 12px 16px calc(12px + env(safe-area-inset-top, 0px));
  border-bottom: 1px solid var(--border);
}
.detail-page .sheet-title { font-size: 1rem; }
.detail-back { width: 38px; height: 38px; flex-shrink: 0; }
[dir="rtl"] .detail-back svg { transform: scaleX(-1); }

.detail-page .detail-card {
  flex: 1; min-height: 0;
  display: flex; flex-direction: column;
  gap: 14px;
  overflow: hidden;
  padding: 16px 20px calc(20px + env(safe-area-inset-bottom, 0px));
}

/* The ring is the same component, just not the centrepiece any more — the
   page has to fit six facts, four actions and a note under it. */
.detail-page .countdown-ring-wrap { margin: 0; }
.detail-page .countdown-ring { width: 104px; height: 104px; }
.detail-page .ring-days { font-size: 1.9rem; }

.detail-page .detail-meta-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px; margin: 0;
}
.detail-page .detail-meta-box { padding: 9px 11px; }
.detail-page .detail-meta-label { font-size: 0.7rem; }
.detail-page .detail-meta-box strong { font-size: 0.82rem; }

.detail-page .detail-actions { margin: 0; }

.detail-page .field-group {
  flex: 1; min-height: 96px;
  display: flex; flex-direction: column;
  margin: 0;
}
.detail-page .field-textarea {
  flex: 1; min-height: 72px;
  resize: none;
  overflow-y: auto;
}
.detail-page .form-actions { flex-shrink: 0; margin-top: 12px; }

/* A page does not need the sheet's dimmed backdrop behind it. */
.detail-page:not([hidden]) ~ .sheet-overlay { display: none; }
'''

append("static/style.css", STYLES, "Batch 13d — the detail view", "detail page layout")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13d (detail view as a fixed page)\n")
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
    print("      git add -A && git commit -m 'Batch 13d: event detail as a fixed page'")
    print()
    print("  Open an event with a long note on a real phone: the page must not")
    print("  move, and only the note box should scroll under your thumb.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
