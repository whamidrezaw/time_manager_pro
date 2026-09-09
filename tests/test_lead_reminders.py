from __future__ import annotations

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
