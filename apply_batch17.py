#!/usr/bin/env python3
"""
apply_batch17.py — TimeManager Pro, Batch 17

  * a checklist on every event, in its own field, next to the note
  * deleting a shared event now says what it is about to do to other people

The note and the checklist share the bottom of the detail page as two tabs.
That is not decoration: the page is fixed height by design, and stacking a
second block under the note is exactly what pushed the note off the screen
last time.

Run once from the repository root:

    python apply_batch17.py --check   # dry run, writes nothing
    python apply_batch17.py           # apply

Requires Batch 16. Safe to run twice.
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
              "— is Batch 16 applied?")
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
        path.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 1. Schemas
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/schemas/common.py",
    old="""class ReminderSpec(APIModel):
""",
    new="""class ChecklistItem(APIModel):
    \"\"\"One line of an event's checklist.

    Its own field rather than lines inside the note: a checkbox that has to be
    parsed back out of free text breaks the moment someone edits the text
    around it.
    \"\"\"

    text: str = Field(min_length=1, max_length=200)
    done: bool = False


class ReminderSpec(APIModel):
""",
    marker="class ChecklistItem",
    label="the checklist item",
)

patch(
    "app/schemas/requests.py",
    old="""class SaveNoteRequest(EventIdPayload):
""",
    new="""class SaveChecklistRequest(EventIdPayload):
    # Capped because this is one document field, not a todo app: fifty lines
    # is already more than anyone reads on an event card.
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=50)


class SaveNoteRequest(EventIdPayload):
""",
    marker="class SaveChecklistRequest",
    label="the checklist request",
)

patch(
    "app/schemas/requests.py",
    old="""    EventIdPayload,
""",
    new="""    ChecklistItem,
    EventIdPayload,
""",
    marker="ChecklistItem,\n    EventIdPayload,",
    label="import the item model",
)

patch(
    "app/schemas/responses.py",
    old="from app.schemas.common import APIModel, PaginationMeta, ReminderSpec, SuccessResponse\n",
    new=(
        "from app.schemas.common import (\n"
        "    APIModel,\n"
        "    ChecklistItem,\n"
        "    PaginationMeta,\n"
        "    ReminderSpec,\n"
        "    SuccessResponse,\n"
        ")\n"
    ),
    marker="    ChecklistItem,\n    PaginationMeta,",
    label="import the item model in responses",
)

patch(
    "app/schemas/responses.py",
    old="""    share_role: str | None = None
""",
    new="""    share_role: str | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list)
""",
    marker="checklist: list[ChecklistItem]",
    label="checklist on the response",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Saving it
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/events.py",
    old="""async def set_pin_for_user(user_id: str, payload: PinEventRequest) -> bool:
""",
    new="""async def save_checklist_for_user(
    user_id: str,
    payload: SaveChecklistRequest,
) -> list[dict]:
    \"\"\"Replace the whole checklist in one write.

    Sending the list rather than a diff means ticking a box and deleting a
    line take the same path, and two taps in quick succession cannot
    interleave into a half-applied state.
    \"\"\"
    events_coll = get_events_collection()

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    items = [
        {"text": item.text.strip(), "done": bool(item.done)}
        for item in payload.checklist
        if item.text.strip()
    ]

    result = await events_coll.update_one(
        {"_id": oid, "user_id": user_id},
        {"$set": {"checklist": items, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    return items


async def set_pin_for_user(user_id: str, payload: PinEventRequest) -> bool:
""",
    marker="async def save_checklist_for_user",
    label="save the checklist",
)

patch(
    "app/services/events.py",
    old="""        share_role=doc.get("share_role"),
    )
""",
    new="""        share_role=doc.get("share_role"),
        # Personal, exactly like the note: a shared birthday does not carry
        # one person's shopping list onto everyone else's copy.
        checklist=[
            item for item in (doc.get("checklist") or [])
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ],
    )
""",
    marker="one person's shopping list",
    label="return the checklist",
)

patch(
    "app/services/events.py",
    old="""    SaveNoteRequest,
""",
    new="""    SaveChecklistRequest,
    SaveNoteRequest,
""",
    marker="SaveChecklistRequest,\n    SaveNoteRequest,",
    label="import the request model",
)

patch(
    "app/routes/events.py",
    old="""@router.post("/pin", response_model=PinResponse)
""",
    new="""@router.post("/checklist")
async def api_checklist(request: Request, payload: SaveChecklistRequest) -> dict:
    user_id = await get_authenticated_user_id(request, payload.initData)
    items = await save_checklist_for_user(user_id, payload)
    return {"success": True, "checklist": items}


@router.post("/pin", response_model=PinResponse)
""",
    marker='@router.post("/checklist")',
    label="the checklist endpoint",
)

patch(
    "app/routes/events.py",
    old="""    save_note_for_user,
""",
    new="""    save_checklist_for_user,
    save_note_for_user,
""",
    marker="save_checklist_for_user,\n    save_note_for_user,",
    label="import the service function",
)

patch(
    "app/routes/events.py",
    old="""    SaveNoteRequest,
""",
    new="""    SaveChecklistRequest,
    SaveNoteRequest,
""",
    marker="SaveChecklistRequest,\n    SaveNoteRequest,",
    label="import the checklist request",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Two tabs at the bottom of the detail page
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""      <div class="field-group">
        <label class="field-label" for="detailNote">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          Event Note
        </label>
""",
    new="""      <div class="field-group">
        <div class="pane-tabs" role="tablist">
          <button type="button" class="pane-tab is-active" data-pane="note" role="tab" aria-selected="true">
            <span data-i18n>Note</span>
          </button>
          <button type="button" class="pane-tab" data-pane="checklist" role="tab" aria-selected="false">
            <span data-i18n>Checklist</span>
            <span class="pane-count" id="checklistCount" hidden></span>
          </button>
        </div>
""",
    marker='class="pane-tabs"',
    label="note and checklist as tabs",
)

patch(
    "templates/index.html",
    old="""        <textarea id="detailNote" class="field-input field-textarea" rows="6" maxlength="2000" placeholder="Write a note, checklist, or details…"></textarea>
      </div>
""",
    new="""        <textarea id="detailNote" class="field-input field-textarea" rows="6" maxlength="2000" placeholder="Write a note or any details…"></textarea>
        <div class="checklist" id="checklistPane" hidden>
          <div class="checklist-items" id="checklistItems"></div>
          <div class="checklist-add">
            <input type="text" id="checklistInput" class="field-input" maxlength="200" placeholder="Add an item" />
            <button type="button" class="btn-primary checklist-add-btn" id="checklistAdd" aria-label="Add item">+</button>
          </div>
        </div>
      </div>
""",
    marker='id="checklistPane"',
    label="the checklist pane",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The checklist in the app
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""    if (els.detailNote)         els.detailNote.value                = ev.note        || "";
""",
    new="""    if (els.detailNote)         els.detailNote.value                = ev.note        || "";
    checklist = Array.isArray(ev.checklist) ? ev.checklist.map(function (i) {
      return { text: String(i.text || ""), done: !!i.done };
    }) : [];
    renderChecklist();
    showPane("note");
""",
    marker="renderChecklist();\n    showPane(\"note\");",
    label="load the checklist with the event",
)

CHECKLIST_JS = r'''  /* ── Checklist ───────────────────────────────────────
     Its own field on the event rather than lines inside the note. A checkbox
     parsed back out of free text breaks the first time somebody edits the
     text around it, and this one has to survive being edited every day. */

  let checklist = [];

  function showPane(name) {
    document.querySelectorAll(".pane-tab").forEach((tab) => {
      const active = tab.dataset.pane === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    if (els.detailNote) els.detailNote.hidden = name !== "note";

    const pane = document.getElementById("checklistPane");
    if (pane) pane.hidden = name !== "checklist";

    // Reset and Save belong to the note. The checklist saves itself on every
    // tick, because a checkbox that needs a second button is a checkbox
    // people forget to save.
    const actions = document.querySelector("#detailSheet .form-actions");
    if (actions) actions.hidden = name !== "note";
  }

  function renderChecklist() {
    const wrap = document.getElementById("checklistItems");
    const count = document.getElementById("checklistCount");
    if (!wrap) return;

    wrap.replaceChildren();
    checklist.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "check-row" + (item.done ? " is-done" : "");

      const box = document.createElement("button");
      box.type = "button";
      box.className = "check-box";
      box.setAttribute("role", "checkbox");
      box.setAttribute("aria-checked", String(!!item.done));
      box.innerHTML = item.done
        ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>'
        : "";
      box.addEventListener("click", () => {
        checklist[index].done = !checklist[index].done;
        try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
        renderChecklist();
        saveChecklist();
      });

      const label = document.createElement("span");
      label.className = "check-text";
      label.textContent = item.text;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "check-remove";
      remove.setAttribute("aria-label", t("Remove item"));
      remove.textContent = "✕";
      remove.addEventListener("click", () => {
        checklist.splice(index, 1);
        renderChecklist();
        saveChecklist();
      });

      row.append(box, label, remove);
      wrap.appendChild(row);
    });

    if (count) {
      const done = checklist.filter((item) => item.done).length;
      count.textContent = checklist.length ? `${done}/${checklist.length}` : "";
      count.hidden = !checklist.length;
    }
  }

  function addChecklistItem() {
    const input = document.getElementById("checklistInput");
    const text = (input?.value || "").trim();
    if (!text) return;
    if (checklist.length >= 50) {
      showToast(t("That is as long as a checklist gets."), "error");
      return;
    }

    checklist.push({ text, done: false });
    input.value = "";
    renderChecklist();
    saveChecklist();
    input.focus();
  }

  async function saveChecklist() {
    if (!state.detailEventId) return;
    try {
      await apiPost("/api/checklist", {
        event_id: state.detailEventId,
        checklist,
      });
      const target = getEventById(state.detailEventId);
      if (target) target.checklist = checklist.map((item) => ({ ...item }));
    } catch (error) {
      showToast(normalizeError(error), "error");
    }
  }

  function bindChecklist() {
    document.querySelectorAll(".pane-tab").forEach((tab) => {
      tab.addEventListener("click", () => showPane(tab.dataset.pane));
    });
    document.getElementById("checklistAdd")?.addEventListener("click", addChecklistItem);
    document.getElementById("checklistInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addChecklistItem(); }
    });
  }

'''

patch(
    "static/app.js",
    old="""  /* ── Published surface ───────────────────────────────
""",
    new=CHECKLIST_JS + """  /* ── Published surface ───────────────────────────────
""",
    marker="/* ── Checklist ─",
    label="the checklist logic",
)

patch(
    "static/app.js",
    old="""  bindDatePicker();
""",
    new="""  bindDatePicker();
  bindChecklist();
""",
    marker="bindChecklist();",
    label="bind it on boot",
)

# ══════════════════════════════════════════════════════════════════════
# 5. The delete warning
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/app.js",
    old="""      text:    `"${ev.title}" will be permanently removed.`,
""",
    new="""      // Batch 12c made this delete reach other people's accounts. A dialog
      // that does not say so is worse than no dialog.
      text: ev.share_role === "owner"
        ? t("This removes it for everyone you shared it with, not only for you.")
        : `"${ev.title}" will be permanently removed.`,
""",
    marker="This removes it for everyone you shared it with",
    label="say what deleting a shared event does",
)

patch(
    "static/app.js",
    old="""      "monthly until then": "ماهانه تا آن روز",
""",
    new="""      "monthly until then": "ماهانه تا آن روز",
      "Note": "یادداشت",
      "Checklist": "چک‌لیست",
      "Add an item": "افزودن مورد",
      "Remove item": "حذف مورد",
      "That is as long as a checklist gets.": "چک‌لیست از این بلندتر نمی‌شود.",
      "This removes it for everyone you shared it with, not only for you.":
        "این رویداد برای همهٔ کسانی که با آن‌ها مشترکش کرده‌ای هم حذف می‌شود، نه فقط برای تو.",
""",
    marker='"Checklist": "چک‌لیست"',
    label="Persian for the checklist",
)

# ══════════════════════════════════════════════════════════════════════
# 6. Styles
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 17 — note and checklist share the space ───── */

/* Two tabs rather than two stacked blocks. The detail page is fixed height by
   design, and stacking a second block under the note is exactly what pushed
   the note off the screen the last time. */
.pane-tabs {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 10px;
}
.pane-tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px;
  border: none; border-radius: var(--r-pill);
  background: var(--surface-2);
  color: var(--text-muted);
  font-family: inherit; font-size: 0.8rem; font-weight: 800;
  cursor: pointer;
}
.pane-tab.is-active { background: rgba(91, 108, 248, 0.14); color: var(--brand); }
.pane-count {
  padding: 1px 7px;
  border-radius: var(--r-pill);
  background: var(--brand); color: #fff;
  font-size: 0.68rem;
}

.checklist { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.checklist-items { flex: 1; min-height: 0; overflow-y: auto; }

.check-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px solid var(--border);
}
.check-box {
  flex-shrink: 0;
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 2px solid var(--border-strong, var(--border));
  border-radius: 7px;
  background: none; color: #fff;
  cursor: pointer;
}
.check-row.is-done .check-box { background: var(--brand); border-color: var(--brand); }
.check-text { flex: 1; min-width: 0; font-size: 0.88rem; word-break: break-word; }
.check-row.is-done .check-text { color: var(--text-muted); text-decoration: line-through; }
.check-remove {
  flex-shrink: 0;
  width: 28px; height: 28px;
  border: none; background: none;
  color: var(--text-muted);
  font-size: 0.8rem; cursor: pointer;
}

.checklist-add { display: flex; gap: 8px; margin-top: 10px; }
.checklist-add .field-input { flex: 1; min-width: 0; }
.checklist-add-btn { width: 48px; flex-shrink: 0; font-size: 1.2rem; padding: 0; }
'''

append("static/style.css", STYLES, "Batch 17 — note and checklist", "checklist styles")

# ══════════════════════════════════════════════════════════════════════
# 7. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.common import ChecklistItem
from app.schemas.requests import SaveChecklistRequest
from app.services.share_group import SHARED_FIELDS


def _request(items) -> SaveChecklistRequest:
    return SaveChecklistRequest(
        initData="x", event_id="507f1f77bcf86cd799439011", checklist=items
    )


def test_an_item_needs_text() -> None:
    with pytest.raises(ValidationError):
        ChecklistItem(text="")


def test_an_item_defaults_to_unticked() -> None:
    assert ChecklistItem(text="Buy the cake").done is False


def test_text_is_capped() -> None:
    with pytest.raises(ValidationError):
        ChecklistItem(text="x" * 201)


def test_a_checklist_is_capped_at_fifty_lines() -> None:
    """One document field, not a todo app."""
    fifty = [{"text": f"item {n}"} for n in range(50)]

    assert len(_request(fifty).checklist) == 50
    with pytest.raises(ValidationError):
        _request(fifty + [{"text": "one too many"}])


def test_an_empty_checklist_is_valid() -> None:
    """Clearing the last item has to be expressible."""
    assert _request([]).checklist == []


def test_the_checklist_is_personal_on_a_shared_event() -> None:
    """The creator owns what the event is; the list of what to buy for it is
    the same kind of private as the note."""
    assert "checklist" not in SHARED_FIELDS
    assert "note" not in SHARED_FIELDS
'''

create("tests/test_checklist.py", TESTS, "checklist tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 17 (checklist, delete warning)\n")
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
    print("      git add -A && git commit -m 'Batch 17: event checklist and delete warning'")
    print()
    print("  Open an event, switch to the checklist tab, tick a box and reopen it.")
    print("  Ticking saves on its own — there is no button for it on purpose.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
