#!/usr/bin/env python3
"""
apply_batch13e.py — TimeManager Pro, Batch 13e

Two fixes reported from a real phone:

  * the note was missing from the detail page — the block above it grew taller
    than the screen and the card clipped it out of sight
  * the "today" button in the month header was too quiet and too close to the
    month name

Run once from the repository root:

    python apply_batch13e.py --check   # dry run, writes nothing
    python apply_batch13e.py           # apply

Requires Batch 13d. Safe to run twice.
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
              "— is Batch 13d applied?")
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
# 1. The today button gets its own row
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/views.js",
    old="""      + '<span class="cal-head-mid"><span class="cal-title">' + meta.label + "</span>"
      + '<button type="button" class="cal-today" id="calToday" aria-label="' + t("Go to today") + '"'
      + (onToday ? " hidden" : "") + ">" + t("Today") + "</button></span>"
      + '<button type="button" class="icon-btn" data-step="1" aria-label="' + t("Next month") + '">›</button>'
      + '</div><div class="cal-dow">';
""",
    new="""      + '<span class="cal-title">' + meta.label + "</span>"
      + '<button type="button" class="icon-btn" data-step="1" aria-label="' + t("Next month") + '">›</button>'
      + "</div>"
      + '<div class="cal-todaybar">'
      + '<button type="button" class="cal-today" id="calToday" aria-label="' + t("Go to today") + '"'
      + (onToday ? " hidden" : "") + ">" + t("Today") + "</button></div>"
      + '<div class="cal-dow">';
""",
    marker='cal-todaybar',
    label="today button on its own row",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Layout
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13e — the note comes back, the today button grows ── */

/* Why the note vanished: everything above it is fixed height, the card clips
   its overflow, and on a short screen the sum of badges, title, ring, six
   fact boxes and four actions was simply taller than the viewport. The note
   was not hidden by a rule — it was pushed past the bottom edge and cut off.
   So the block above it is made shorter, and the note is given a floor that
   nothing else may take. */

.detail-page .detail-card { gap: 10px; }

/* The ring stops being a centrepiece and lies down next to its own label.
   That alone is about a hundred pixels. */
.detail-page .countdown-ring-wrap {
  display: flex; flex-direction: row; align-items: center;
  gap: 14px; margin: 0;
}
.detail-page .countdown-ring { width: 78px; height: 78px; flex-shrink: 0; }
.detail-page .ring-days { font-size: 1.5rem; }
.detail-page .countdown-ring-text { margin: 0; text-align: start; }

.detail-page .detail-event-title { margin: 0; font-size: 1.15rem; }
.detail-page .detail-topline { margin: 0; }
.detail-page .detail-meta-box { padding: 7px 10px; }
.detail-page .detail-meta-box strong { font-size: 0.8rem; }

/* The note's floor. flex-basis 0 with a real min-height means it claims its
   share first and cannot be squeezed to nothing by a long title. */
.detail-page .field-group {
  flex: 1 1 0;
  min-height: 132px;
}
.detail-page .field-textarea { flex: 1; min-height: 84px; }

/* Safety valve. The numbers above should fit any phone, but a very large
   system font can still overflow them — and a card that scrolls a little is
   far better than one that silently swallows the note. */
.detail-page .detail-card { overflow-y: auto; }

/* ── Today button ────────────────────────────────────── */

.cal-head-mid { display: contents; }

.cal-todaybar {
  display: flex; justify-content: flex-start;
  margin: -6px 0 14px;
}
.cal-today {
  padding: 7px 20px;
  border: none;
  border-radius: var(--r-pill);
  background: var(--brand);
  color: #fff;
  font-family: inherit; font-size: 0.85rem; font-weight: 800;
  box-shadow: 0 4px 14px rgba(91, 108, 248, 0.32);
  cursor: pointer;
}
.cal-today:active { transform: scale(0.97); background: var(--brand); }
'''

append("static/style.css", STYLES, "Batch 13e — the note comes back", "detail and today button")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13e (note visibility, today button)\n")
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
    print("      git add -A && git commit -m 'Batch 13e: note visibility and today button'")
    print()
    print("  Open an event with a long note: the note box must be visible without")
    print("  scrolling, and typing in it must not push anything else off screen.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
