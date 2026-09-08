#!/usr/bin/env python3
"""
apply_batch13a2.py — TimeManager Pro, Batch 13a-2 (swipe actions)

Each card is wrapped in a row with a pin button behind one edge and a delete
button behind the other. Dragging reveals them; the button does the work, and
delete still asks first.

Run once from the repository root:

    python apply_batch13a2.py --check   # dry run, writes nothing
    python apply_batch13a2.py           # apply

Requires Batch 13a-1. Safe to run twice.
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
              "— is Batch 13a-1 applied?")
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
# 1. Pin and delete need to work on any event, not just the open one
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""  async function deleteCurrentEvent() {
    const ev = getEventById(state.detailEventId);
""",
    new="""  // Takes an id now so a swipe can reach an event that is not open in the
  // sheet. The default keeps every existing call site working untouched.
  async function deleteCurrentEvent(eventId = state.detailEventId) {
    const ev = getEventById(eventId);
""",
    marker="async function deleteCurrentEvent(eventId",
    label="delete by id",
)

patch(
    "static/app.js",
    old="""  async function toggleCurrentPin() {
    try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch (_) {}
    const ev = getEventById(state.detailEventId);
""",
    new="""  async function toggleCurrentPin(eventId = state.detailEventId) {
    try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch (_) {}
    const ev = getEventById(eventId);
""",
    marker="async function toggleCurrentPin(eventId",
    label="pin by id",
)

# ══════════════════════════════════════════════════════════════════════
# 2. The swipe engine
# ══════════════════════════════════════════════════════════════════════

SWIPE_ENGINE = r'''  function clearCards() {
    if (els.eventsWrap) els.eventsWrap.replaceChildren();
    cardIndex.clear();
    headerIndex.clear();
    openRow = null;
  }

  /* ── Swipe actions ───────────────────────────────────
     Reveal, not commit. Dragging uncovers the buttons and the button does the
     work. In a list whose main gesture is a vertical scroll, a one-step swipe
     that fires on release gets triggered by accident far too often — and the
     one action here that cannot be undone is delete.

     Nothing below needs to know about right-to-left. The card follows the
     finger physically, and the two action strips are placed with
     inset-inline-start/end, so Persian flips them for free. */

  const SWIPE_WIDTH = 84;     // how far a row opens
  const SWIPE_TRIGGER = 40;   // drag past this and it stays open
  const SWIPE_SLOP = 8;       // ignore the first few pixels of any gesture
  let openRow = null;

  const ROW_ICONS = {
    pin: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>',
  };

  function rowActionButton(kind, icon, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `row-action row-action-${kind}`;
    // Behind a closed card these would otherwise sit in the tab order of every
    // row. The detail sheet already offers both actions to keyboard users.
    button.tabIndex = -1;
    button.innerHTML = `${icon}<span class="row-action-label">${label}</span>`;
    return button;
  }

  function wrapInRow(art, id) {
    const row = document.createElement("div");
    row.className = "event-row";
    row.dataset.id = id;

    const startSide = document.createElement("div");
    startSide.className = "event-actions event-actions-start";
    const pinBtn = rowActionButton("pin", ROW_ICONS.pin, t("Pin"));
    startSide.appendChild(pinBtn);

    const endSide = document.createElement("div");
    endSide.className = "event-actions event-actions-end";
    const deleteBtn = rowActionButton("delete", ROW_ICONS.trash, t("Delete"));
    endSide.appendChild(deleteBtn);

    row.append(startSide, endSide, art);
    row._card = art;
    row._pinBtn = pinBtn;
    row._pinLabel = pinBtn.querySelector(".row-action-label");
    row._actions = [pinBtn, deleteBtn];

    pinBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeRow(row);
      toggleCurrentPin(id);
    });
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeRow(row);
      // deleteCurrentEvent asks for confirmation itself, which is exactly the
      // popup the swipe path is required to show.
      deleteCurrentEvent(id);
    });

    bindSwipe(row);
    return row;
  }

  function setRowOffset(row, offset, animate = false) {
    const card = row._card;
    // An empty string would fall back to the 150ms transform transition on
    // .event-card and make the card lag a dragging finger.
    card.style.transition = animate
      ? "transform 220ms cubic-bezier(0.22, 1, 0.36, 1)"
      : "none";
    card.style.transform = offset ? `translateX(${offset}px)` : "";

    const isOpen = Math.abs(offset) > 1;
    row.classList.toggle("is-open", isOpen);
    row._actions.forEach((button) => { button.tabIndex = isOpen ? 0 : -1; });
  }

  function closeRow(row, animate = true) {
    if (!row) return false;
    setRowOffset(row, 0, animate);
    if (openRow === row) openRow = null;
    return true;
  }

  function closeOpenRow() {
    if (!openRow) return false;
    closeRow(openRow);
    return true;
  }

  function bindSwipe(row) {
    const card = row._card;
    let startX = 0, startY = 0, delta = 0;
    let pointerId = null, decided = false, dragging = false;

    card.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pointerId = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      delta = 0;
      decided = false;
      dragging = false;
      row._swiped = false;
    });

    card.addEventListener("pointermove", (e) => {
      if (e.pointerId !== pointerId) return;
      const mx = e.clientX - startX;
      const my = e.clientY - startY;

      if (!decided) {
        if (Math.abs(mx) < SWIPE_SLOP && Math.abs(my) < SWIPE_SLOP) return;
        decided = true;
        // A gesture that is mostly vertical belongs to the scroller, and once
        // it has been handed over it is never taken back mid-drag.
        dragging = Math.abs(mx) > Math.abs(my) * 1.4;
        if (dragging && openRow && openRow !== row) closeRow(openRow);
      }
      if (!dragging) return;

      e.preventDefault();
      delta = Math.max(-SWIPE_WIDTH, Math.min(SWIPE_WIDTH, mx));
      setRowOffset(row, delta);
    }, { passive: false });

    const settle = (e) => {
      if (e.pointerId !== pointerId) return;
      pointerId = null;
      if (!dragging) return;

      dragging = false;
      row._swiped = true;                 // cleared on the next pointerdown

      if (Math.abs(delta) >= SWIPE_TRIGGER) {
        setRowOffset(row, delta > 0 ? SWIPE_WIDTH : -SWIPE_WIDTH, true);
        openRow = row;
        try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
      } else {
        closeRow(row);
      }
    };

    card.addEventListener("pointerup", settle);
    card.addEventListener("pointercancel", settle);
  }

'''

patch(
    "static/app.js",
    old="""  function clearCards() {
    if (els.eventsWrap) els.eventsWrap.replaceChildren();
    cardIndex.clear();
    headerIndex.clear();
  }

""",
    new=SWIPE_ENGINE,
    marker="function bindSwipe(row)",
    label="swipe engine",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Hook the row into the renderer
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    const open = () => openDetail(art.dataset.id);
    art.addEventListener("click", open);
    art.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });

    return art;
  }
""",
    new="""    const open = () => {
      // The click that ends a swipe should put the row back rather than open
      // the sheet — otherwise every drag lands you in the detail view.
      const row = art.parentElement;
      if (row && row._swiped) return;
      if (closeOpenRow()) return;
      openDetail(art.dataset.id);
    };
    art.addEventListener("click", open);
    art.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });

    return wrapInRow(art, id);
  }
""",
    marker="return wrapInRow(art, id);",
    label="cards live inside a row",
)

patch(
    "static/app.js",
    old="""  function fillCard(art, event, cd) {
    const f = art._f;
""",
    new="""  function fillCard(row, event, cd) {
    const art = row._card || row;
    const f = art._f;

    if (row._pinLabel) {
      const pinLabel = t(event.pinned ? "Unpin" : "Pin");
      row._pinLabel.textContent = pinLabel;
      row._pinBtn.setAttribute("aria-label", `${pinLabel}: ${event.title || ""}`);
      row._actions[1].setAttribute("aria-label", `${t("Delete")}: ${event.title || ""}`);
      row.classList.toggle("row-pinned", !!event.pinned);
    }
""",
    marker="const art = row._card || row;",
    label="fill through the row",
)

patch(
    "static/app.js",
    old="""    const animate = !prefersReducedMotion();
""",
    new="""    // A render means the data moved underneath any open row, so the revealed
    // buttons would no longer belong to the card sitting above them.
    closeOpenRow();

    const animate = !prefersReducedMotion();
""",
    marker="closeOpenRow();\n\n    const animate",
    label="close an open row on render",
)

# ══════════════════════════════════════════════════════════════════════
# 4. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 13a-2 — swipe actions ─────────────────────── */

.event-row {
  position: relative;
  border-radius: var(--r-lg);
}

/* Matching the card's radius exactly is what stops the coloured strips from
   peeking out at the rounded corners while the row is closed. */
.event-row .event-card {
  position: relative;
  z-index: 1;
  touch-action: pan-y;
  will-change: transform;
}

.event-actions {
  position: absolute;
  inset-block: 0;
  z-index: 0;
  display: flex;
  border-radius: var(--r-lg);
  overflow: hidden;
}
.event-actions-start { inset-inline-start: 0; }
.event-actions-end   { inset-inline-end: 0; }

.row-action {
  width: 84px;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px;
  border: none; cursor: pointer;
  color: #fff;
  font-size: 0.72rem; font-weight: 800;
  font-family: inherit;
}
.row-action-pin    { background: linear-gradient(160deg, #ec4899, #be185d); }
.row-action-delete { background: linear-gradient(160deg, #f87171, #dc2626); }
.row-action:active { filter: brightness(0.92); }
.row-action:focus-visible { outline: 3px solid var(--brand); outline-offset: -3px; }
.row-action svg { opacity: 0.95; }

/* Once a row is open the card is the only thing standing between a stray tap
   and a delete, so it stops behaving like a link. */
.event-row.is-open .event-card { cursor: default; }

@media (prefers-reduced-motion: reduce) {
  .event-row .event-card { will-change: auto; }
}
'''

append("static/style.css", STYLES, "Batch 13a-2 — swipe actions", "swipe styles")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 13a-2 (swipe actions)\n")
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
    print("      git add -A && git commit -m 'Batch 13a-2: swipe actions on event cards'")
    print()
    print("  Front-end only. Swipe a card both ways on a real phone before")
    print("  trusting it — a pointer gesture cannot be checked from the shell.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
