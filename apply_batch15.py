#!/usr/bin/env python3
"""
apply_batch15.py — TimeManager Pro, Batch 15 (lead reminders)

"Repeat" has always meant "the event happens again". What was missing is the
other thing entirely: nudge me every week until the day arrives, then stop.
A bank instalment two months out should not wait two months to say anything.

So the two ideas get two fields:

    repeat       — does this event happen again?  (unchanged)
    lead_repeat  — how often should I be reminded until it does?

A lead reminder is expanded into ordinary reminder specs at save time, so the
scheduler and the worker need no new concept, and none of them draw a dot in
the calendar.

Run once from the repository root:

    python apply_batch15.py --check   # dry run, writes nothing
    python apply_batch15.py           # apply

Requires Batch 13e. Safe to run twice.
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
              "— is Batch 13e applied?")
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
# 1. dates.py — a third kind of reminder
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/utils/dates.py",
    old="""# A reminder may sit at most 30 days ahead of its event.
MAX_OFFSET_MINUTES = 60 * 24 * 30
""",
    new="""# A reminder may sit at most 30 days ahead of its event.
MAX_OFFSET_MINUTES = 60 * 24 * 30

# Lead reminders: the nudges that run up to the event and stop on the day.
# They cannot be expressed as relative reminders because those are capped at
# 30 days and are dropped for all-day events, and "remind me weekly for three
# months about my instalment" is exactly an all-day event 84 days out.
LEAD_VALUES = {"none", "daily", "weekly", "monthly"}
LEAD_STEP_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
# Twelve steps: a weekly lead starts about three months out, a daily one
# twelve days out. Enough to be useful, few enough not to become noise.
MAX_LEAD_REMINDERS = 12
MAX_LEAD_DAYS = 400
""",
    marker="MAX_LEAD_REMINDERS",
    label="lead reminder constants",
)

patch(
    "app/utils/dates.py",
    old="""        if str(item.get("mode", "absolute")).lower() == "relative":
""",
    new="""        mode = str(item.get("mode", "absolute")).lower()

        if mode == "lead":
            days = int(item.get("days_before", 0) or 0)
            if days <= 0:
                continue
            specs.append({
                "mode": "lead",
                "days_before": min(days, MAX_LEAD_DAYS),
                "hour": max(0, min(int(item.get("hour", legacy_hour) or 0), 23)),
                "minute": max(0, min(int(item.get("minute", legacy_minute) or 0), 59)),
            })
            continue

        if mode == "relative":
""",
    marker='if mode == "lead":',
    label="accept lead specs",
)

patch(
    "app/utils/dates.py",
    old="""    if all_day:
        specs = [spec for spec in specs if spec["mode"] == "absolute"]
""",
    new="""    if all_day:
        # A lead reminder survives here where a relative one does not: it names
        # a day and a wall-clock time, both of which an all-day event has.
        specs = [spec for spec in specs if spec["mode"] in ("absolute", "lead")]
""",
    marker='spec["mode"] in ("absolute", "lead")',
    label="keep lead specs on all-day events",
)

patch(
    "app/utils/dates.py",
    old="""    if spec.get("mode") == "relative":
        return occurrence_utc - timedelta(minutes=int(spec.get("offset_minutes", 0)))
""",
    new="""    if spec.get("mode") == "lead":
        # Counted in days on the local calendar rather than in hours, so a lead
        # reminder keeps its time of day across a daylight-saving change.
        local = occurrence_utc.astimezone(tz) - timedelta(
            days=int(spec.get("days_before", 0))
        )
        return local.replace(
            hour=int(spec.get("hour", DEFAULT_REMINDER_HOUR)),
            minute=int(spec.get("minute", DEFAULT_REMINDER_MINUTE)),
            second=0,
            microsecond=0,
        ).astimezone(timezone.utc)

    if spec.get("mode") == "relative":
        return occurrence_utc - timedelta(minutes=int(spec.get("offset_minutes", 0)))
""",
    marker='if spec.get("mode") == "lead":',
    label="fire time for a lead reminder",
)

patch(
    "app/utils/dates.py",
    old="""    if repeat == "none":
        # Unchanged behaviour for one-off events: if the reminder time has
        # already passed, it still fires on the next worker pass rather than
        # being dropped silently.
        return occurrence, earliest_fire(reminders, occurrence, tz)
""",
    new="""    if repeat == "none":
        # Prefer a reminder that is still ahead. Lead reminders sit weeks before
        # the event, so on an event created close to its date several of them
        # are already in the past — firing one of those the moment the event is
        # saved would be a notification about nothing.
        ahead = earliest_fire(reminders, occurrence, tz, after=now)
        # The unfiltered fallback is the old behaviour, kept: an event whose
        # only reminder time has just passed still fires on the next pass
        # rather than being dropped silently.
        return occurrence, ahead or earliest_fire(reminders, occurrence, tz)
""",
    marker="firing one of those the moment the event is",
    label="do not fire a lead reminder that is already past",
)

append(
    "app/utils/dates.py",
    addition='''

def build_lead_reminders(
    lead_repeat: str | None,
    reminders: list[dict],
    max_count: int = MAX_LEAD_REMINDERS,
) -> list[dict]:
    """Turn "remind me weekly until it arrives" into ordinary reminder specs.

    Expanding here rather than teaching the scheduler a new concept is what
    keeps next_schedule, earliest_fire and the worker untouched: to all of
    them these are simply more reminders on the same occurrence, and the
    worker already drains an occurrence before moving to the next one.

    The clock time is copied from the event's own reminder, so a lead reminder
    never arrives at an hour the user did not choose.
    """
    step = LEAD_STEP_DAYS.get(str(lead_repeat or "none").lower())
    if not step:
        return []

    hour, minute = DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE
    for spec in reminders:
        if spec.get("mode") == "absolute":
            hour, minute = int(spec["hour"]), int(spec["minute"])
            break

    return [
        {"mode": "lead", "days_before": step * n, "hour": hour, "minute": minute}
        for n in range(1, max(0, max_count) + 1)
        if step * n <= MAX_LEAD_DAYS
    ]
''',
    marker="def build_lead_reminders",
    label="build the lead reminder specs",
)

# ══════════════════════════════════════════════════════════════════════
# 2. Schemas
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/schemas/common.py",
    old="""    mode: Literal["absolute", "relative"] = "absolute"
""",
    new="""    mode: Literal["absolute", "relative", "lead"] = "absolute"
    # Only set on mode="lead": how many days before the event it fires.
    days_before: int = Field(default=0, ge=0, le=400)
""",
    marker="days_before",
    label="lead mode on the reminder spec",
)

patch(
    "app/schemas/requests.py",
    old="""    repeat: RepeatType = "none"
    repeat_until: str | None = Field(default=None)
""",
    new="""    repeat: RepeatType = "none"
    repeat_until: str | None = Field(default=None)
    # Deliberately separate from `repeat`: one answers "does this happen
    # again", the other "how often should I hear about it until it does".
    lead_repeat: Literal["none", "daily", "weekly", "monthly"] = "none"
""",
    marker="lead_repeat",
    label="lead_repeat on the request",
)

patch(
    "app/schemas/responses.py",
    old="""    repeat_until: str | None = None
""",
    new="""    repeat_until: str | None = None
    lead_repeat: str = "none"
""",
    marker="lead_repeat",
    label="lead_repeat on the response",
)

# ══════════════════════════════════════════════════════════════════════
# 3. Saving and serialising
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/events.py",
    old="""    tz, tz_name = safe_zoneinfo(payload.timezone)
""",
    new="""    # The nudges that run up to the event. Appended to the same list the
    # scheduler already reads, so nothing downstream needs to know they exist.
    lead_repeat = str(getattr(payload, "lead_repeat", "none") or "none")
    reminders = reminders + build_lead_reminders(lead_repeat, reminders)

    tz, tz_name = safe_zoneinfo(payload.timezone)
""",
    marker="build_lead_reminders(lead_repeat, reminders)",
    label="expand lead reminders on save",
)

patch(
    "app/services/events.py",
    old="""        "repeat_until":         repeat_until,
""",
    new="""        "repeat_until":         repeat_until,
        "lead_repeat":          lead_repeat,
""",
    marker='"lead_repeat":          lead_repeat,',
    label="store lead_repeat",
)

patch(
    "app/services/events.py",
    old="""        repeat_until=doc.get("repeat_until"),
    )
""",
    new="""        repeat_until=doc.get("repeat_until"),
        lead_repeat=doc.get("lead_repeat", "none"),
    )
""",
    marker='lead_repeat=doc.get("lead_repeat"',
    label="return lead_repeat",
)

patch(
    "app/services/events.py",
    old="""from app.utils.dates import (
    expire_for_repeat,
""",
    new="""from app.utils.dates import (
    build_lead_reminders,
    expire_for_repeat,
""",
    marker="build_lead_reminders,\n    expire_for_repeat,",
    label="import the builder",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The form
# ══════════════════════════════════════════════════════════════════════

patch(
    "templates/index.html",
    old="""            <option value="yearly">Yearly</option>
          </select>
        </div>
""",
    new="""            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="field-group">
          <label class="field-label" for="leadRepeat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            <span data-i18n>Remind me until then</span>
          </label>
          <select id="leadRepeat" class="field-input field-select">
            <option value="none">Only on the day</option>
            <option value="daily">Every day</option>
            <option value="weekly">Every week</option>
            <option value="monthly">Every month</option>
          </select>
          <p class="field-hint" data-i18n>Reminders before the event. They stop once the day arrives.</p>
        </div>
""",
    marker='id="leadRepeat"',
    label="the lead reminder field",
)

patch(
    "static/app.js",
    old="""      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
""",
    new="""      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
      lead_repeat: document.getElementById("leadRepeat")?.value || "none",
""",
    marker='lead_repeat: document.getElementById("leadRepeat")',
    label="send lead_repeat",
)

patch(
    "static/app.js",
    old="""    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
""",
    new="""    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
    const leadSelect = document.getElementById("leadRepeat");
    if (leadSelect) leadSelect.value = event.lead_repeat || "none";
""",
    marker='const leadSelect = document.getElementById("leadRepeat")',
    label="round-trip lead_repeat into the editor",
)

patch(
    "static/app.js",
    old="""    const reminderBox = document.getElementById("detailReminder");
    if (reminderBox) {
      reminderBox.textContent = reminderTimeText(ev) || t("Before the event");
    }
""",
    new="""    const reminderBox = document.getElementById("detailReminder");
    if (reminderBox) {
      const at = reminderTimeText(ev) || t("Before the event");
      const lead = LEAD_LABELS[ev.lead_repeat] || "";
      reminderBox.textContent = lead ? `${at} · ${t(lead)}` : at;
    }
""",
    marker="LEAD_LABELS[ev.lead_repeat]",
    label="show the lead cadence on the detail page",
)

patch(
    "static/app.js",
    old="""  const STATUS_LABELS = {
""",
    new="""  // Shown next to the reminder time on the detail page, so it is obvious at a
  // glance that an event will speak up before its day and not only on it.
  const LEAD_LABELS = {
    daily: "daily until then",
    weekly: "weekly until then",
    monthly: "monthly until then",
  };

  const STATUS_LABELS = {
""",
    marker="const LEAD_LABELS",
    label="labels for the cadence",
)

patch(
    "static/app.js",
    old="""      "You have reached your event limit. Invite friends to raise it.": "به سقف رویدادهایتان رسیده‌اید. با دعوت دوستان آن را بالا ببرید.",
""",
    new="""      "You have reached your event limit. Invite friends to raise it.": "به سقف رویدادهایتان رسیده‌اید. با دعوت دوستان آن را بالا ببرید.",
      "Remind me until then": "تا آن روز یادم بینداز",
      "Reminders before the event. They stop once the day arrives.": "یادآوری‌های قبل از رویداد. با رسیدن آن روز تمام می‌شوند.",
      "Only on the day": "فقط روز رویداد",
      "Every day": "هر روز",
      "Every week": "هر هفته",
      "Every month": "هر ماه",
      "daily until then": "روزانه تا آن روز",
      "weekly until then": "هفتگی تا آن روز",
      "monthly until then": "ماهانه تا آن روز",
""",
    marker="تا آن روز یادم بینداز",
    label="Persian for the new copy",
)

# ══════════════════════════════════════════════════════════════════════
# 5. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.utils.dates import (
    MAX_LEAD_REMINDERS,
    build_lead_reminders,
    earliest_fire,
    first_schedule,
    normalize_reminders,
    reminder_fire_time,
)

TZ = ZoneInfo("Europe/Berlin")
NINE_AM = [{"mode": "absolute", "hour": 9, "minute": 0}]


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


# ── Building them ────────────────────────────────────────────────────

def test_none_produces_no_lead_reminders() -> None:
    assert build_lead_reminders("none", NINE_AM) == []
    assert build_lead_reminders(None, NINE_AM) == []


@pytest.mark.parametrize(
    ("cadence", "step"), [("daily", 1), ("weekly", 7), ("monthly", 30)]
)
def test_each_cadence_walks_back_in_its_own_step(cadence, step) -> None:
    specs = build_lead_reminders(cadence, NINE_AM)

    assert len(specs) == MAX_LEAD_REMINDERS
    assert [s["days_before"] for s in specs] == [step * n for n in range(1, 13)]
    assert all(s["mode"] == "lead" for s in specs)


def test_the_clock_time_comes_from_the_event_reminder() -> None:
    """A nudge must never arrive at an hour the user did not choose."""
    specs = build_lead_reminders("weekly", [{"mode": "absolute", "hour": 7, "minute": 45}])

    assert all(s["hour"] == 7 and s["minute"] == 45 for s in specs)


def test_a_timed_event_falls_back_to_the_default_hour() -> None:
    specs = build_lead_reminders("weekly", [{"mode": "relative", "offset_minutes": 30}])

    assert all(s["hour"] == 9 and s["minute"] == 0 for s in specs)


# ── Surviving normalisation ──────────────────────────────────────────

def test_lead_specs_survive_on_an_all_day_event() -> None:
    """The reported case: an all-day instalment two months out.

    Relative reminders are stripped for all-day events, which is why the lead
    reminder had to be its own mode rather than a very large offset.
    """
    raw = NINE_AM + [{"mode": "lead", "days_before": 7, "hour": 9, "minute": 0}]

    specs = normalize_reminders(raw, all_day=True)

    assert sum(1 for s in specs if s["mode"] == "lead") == 1


def test_a_relative_spec_is_still_stripped_on_an_all_day_event() -> None:
    raw = NINE_AM + [{"mode": "relative", "offset_minutes": 30}]

    assert all(s["mode"] != "relative" for s in normalize_reminders(raw, all_day=True))


def test_a_zero_day_lead_is_dropped() -> None:
    raw = [{"mode": "lead", "days_before": 0, "hour": 9, "minute": 0}]

    assert all(s["mode"] != "lead" for s in normalize_reminders(raw, all_day=True))


# ── Firing ───────────────────────────────────────────────────────────

def test_a_lead_reminder_fires_that_many_days_earlier_at_the_same_time() -> None:
    occurrence = _at("2026-11-20T00:00")  # local midnight, all-day event
    spec = {"mode": "lead", "days_before": 14, "hour": 9, "minute": 0}

    fire = reminder_fire_time(spec, occurrence, TZ).astimezone(TZ)

    assert (fire.year, fire.month, fire.day) == (2026, 11, 6)
    assert (fire.hour, fire.minute) == (9, 0)


def test_the_next_nudge_is_the_closest_one_still_ahead() -> None:
    """Two months out, weekly: the first thing to fire is eight weeks before,
    not twelve — the earlier ones are already in the past."""
    occurrence = _at("2026-11-20T08:00")
    reminders = NINE_AM + build_lead_reminders("weekly", NINE_AM)
    now = _at("2026-09-20T12:00")

    fire = earliest_fire(reminders, occurrence, TZ, after=now)

    assert fire is not None
    assert fire.astimezone(TZ).date().isoformat() == "2026-09-25"


def test_saving_a_far_off_event_does_not_fire_anything_immediately() -> None:
    """The bug this whole batch exists for, from the other side: creating an
    event must schedule the next nudge, not one from twelve weeks ago."""
    reminders = NINE_AM + build_lead_reminders("weekly", NINE_AM)
    now = _at("2026-09-20T12:00")

    _, notify = first_schedule(
        date_str="2026-11-20", tz=TZ, all_day=True, time_hm=None,
        reminders=reminders, repeat="none", now=now,
    )

    assert notify is not None
    assert notify > now


def test_the_event_day_itself_is_still_the_last_word() -> None:
    """After the nudges, the real reminder — and then nothing."""
    reminders = NINE_AM + build_lead_reminders("weekly", NINE_AM)
    occurrence = _at("2026-11-20T00:00")
    day_before = _at("2026-11-19T12:00")

    fire = earliest_fire(reminders, occurrence, TZ, after=day_before)

    assert fire is not None
    assert fire.astimezone(TZ).date().isoformat() == "2026-11-20"
    assert earliest_fire(reminders, occurrence, TZ, after=_at("2026-11-20T23:00")) is None
'''

create("tests/test_lead_reminders.py", TESTS, "lead reminder tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 15 (lead reminders)\n")
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
    print("      git add -A && git commit -m 'Batch 15: reminders that lead up to the event'")
    print()
    print("  Existing events are untouched: no lead_repeat means no lead reminders,")
    print("  and they keep the exact schedule they have now.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
