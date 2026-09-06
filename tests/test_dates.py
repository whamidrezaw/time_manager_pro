from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.utils.dates import (
    advance_occurrence,
    build_occurrence,
    earliest_fire,
    expire_for_repeat,
    first_schedule,
    format_time_hm,
    next_schedule,
    normalize_reminders,
    parse_time_hm,
    reminder_fire_time,
    safe_zoneinfo,
    shift_month,
    to_jalali,
)

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


# ── Timezones and calendars ─────────────────────────────────────────────────

def test_safe_zoneinfo_returns_valid_zone() -> None:
    tz, name = safe_zoneinfo("Europe/Berlin")
    assert name == "Europe/Berlin"
    assert tz is not None


def test_safe_zoneinfo_falls_back_to_utc() -> None:
    tz, name = safe_zoneinfo("Not/AZone")
    assert name == "UTC"
    assert tz is not None


def test_to_jalali_returns_formatted_value() -> None:
    assert to_jalali("2026-04-20") == "1405/01/31"


def test_to_jalali_fallbacks_for_invalid_input() -> None:
    assert to_jalali("nonsense") == "nonsense"


def test_expire_for_repeat_none_is_future() -> None:
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert expire_for_repeat(anchor, "none") > anchor


# ── Time of day ─────────────────────────────────────────────────────────────

def test_parse_time_hm_roundtrip() -> None:
    assert parse_time_hm("14:30") == (14, 30)
    assert format_time_hm(14, 30) == "14:30"
    assert format_time_hm(9, 5) == "09:05"


def test_parse_time_hm_rejects_bad_values() -> None:
    for bad in ["1430", "25:00", "12:99", ""]:
        try:
            parse_time_hm(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def test_build_occurrence_all_day_anchors_at_local_midnight() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=True)
    assert occurrence.tzinfo is not None
    assert occurrence.astimezone(BERLIN).hour == 0


def test_build_occurrence_uses_the_given_time() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:30")
    local = occurrence.astimezone(BERLIN)
    assert (local.hour, local.minute) == (14, 30)


# ── Recurrence ──────────────────────────────────────────────────────────────

def test_shift_month_reanchors_on_the_original_day() -> None:
    """A monthly event on the 31st must not stick to the 28th after February."""
    jan = datetime(2026, 1, 31, 9, 0)
    feb = shift_month(jan, anchor_day=31, months_ahead=1)
    mar = shift_month(feb, anchor_day=31, months_ahead=1)
    assert (feb.month, feb.day) == (2, 28)
    assert (mar.month, mar.day) == (3, 31)


def test_advance_occurrence_keeps_wall_clock_across_dst() -> None:
    """Berlin leaves DST on 25 October 2026; 09:00 must stay 09:00."""
    before = build_occurrence("2026-10-24", BERLIN, all_day=False, time_hm="09:00")
    after = advance_occurrence(before, "daily", BERLIN, anchor_day=24)
    assert after is not None
    assert after.astimezone(BERLIN).hour == 9


def test_advance_occurrence_returns_none_for_one_off_events() -> None:
    occurrence = build_occurrence("2026-06-15", UTC)
    assert advance_occurrence(occurrence, "none", UTC, anchor_day=15) is None


def test_advance_occurrence_handles_29_february() -> None:
    leap = build_occurrence("2028-02-29", UTC, all_day=False, time_hm="09:00")
    nxt = advance_occurrence(leap, "yearly", UTC, anchor_day=29)
    assert nxt is not None
    assert (nxt.month, nxt.day) == (3, 1)


# ── Reminders ───────────────────────────────────────────────────────────────

def test_normalize_reminders_falls_back_to_the_legacy_pair() -> None:
    specs = normalize_reminders(None, all_day=True, legacy_hour=7, legacy_minute=30)
    assert specs == [{"mode": "absolute", "hour": 7, "minute": 30}]


def test_normalize_reminders_drops_relative_specs_on_all_day_events() -> None:
    specs = normalize_reminders(
        [{"mode": "relative", "offset_minutes": 15}],
        all_day=True,
        legacy_hour=9,
        legacy_minute=0,
    )
    assert specs == [{"mode": "absolute", "hour": 9, "minute": 0}]


def test_normalize_reminders_keeps_relative_specs_on_timed_events() -> None:
    specs = normalize_reminders(
        [{"mode": "relative", "offset_minutes": 60}],
        all_day=False,
    )
    assert specs == [{"mode": "relative", "offset_minutes": 60}]


def test_normalize_reminders_clamps_out_of_range_values() -> None:
    specs = normalize_reminders([{"mode": "absolute", "hour": 99, "minute": -5}], all_day=True)
    assert specs == [{"mode": "absolute", "hour": 23, "minute": 0}]


def test_relative_reminder_fires_before_the_event() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:30")
    fire = reminder_fire_time({"mode": "relative", "offset_minutes": 60}, occurrence, BERLIN)
    assert occurrence - fire == timedelta(minutes=60)


def test_absolute_reminder_fires_on_the_day_of_the_event() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=True)
    fire = reminder_fire_time({"mode": "absolute", "hour": 9, "minute": 0}, occurrence, BERLIN)
    local = fire.astimezone(BERLIN)
    assert (local.date().isoformat(), local.hour) == ("2026-06-15", 9)


def test_earliest_fire_picks_the_soonest_and_can_skip_the_past() -> None:
    occurrence = build_occurrence("2026-06-15", BERLIN, all_day=False, time_hm="14:00")
    specs = [
        {"mode": "relative", "offset_minutes": 60},
        {"mode": "relative", "offset_minutes": 1440},
    ]
    assert earliest_fire(specs, occurrence, BERLIN) == occurrence - timedelta(minutes=1440)

    cutoff = occurrence - timedelta(minutes=120)
    assert earliest_fire(specs, occurrence, BERLIN, after=cutoff) == occurrence - timedelta(minutes=60)


# ── Scheduling ──────────────────────────────────────────────────────────────

def test_first_schedule_for_a_future_one_off_event() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    occurrence, fire = first_schedule(
        date_str="2026-06-15",
        tz=BERLIN,
        all_day=False,
        time_hm="14:30",
        reminders=[{"mode": "relative", "offset_minutes": 30}],
        repeat="none",
        now=now,
    )
    assert fire == occurrence - timedelta(minutes=30)
    assert fire > now


def test_first_schedule_rolls_a_recurring_event_past_now() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    _, fire = first_schedule(
        date_str="2026-06-15",
        tz=UTC,
        all_day=True,
        time_hm=None,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="daily",
        now=now,
    )
    assert fire is not None
    assert fire > now


def test_first_schedule_returns_none_once_repeat_until_has_passed() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    _, fire = first_schedule(
        date_str="2026-06-15",
        tz=UTC,
        all_day=True,
        time_hm=None,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="daily",
        repeat_until="2026-06-15",
        now=now,
    )
    assert fire is None


def test_next_schedule_moves_to_the_following_occurrence() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    occurrence = build_occurrence("2026-06-15", UTC, all_day=True)
    next_occurrence, fire = next_schedule(
        occurrence_utc=occurrence,
        anchor_day=15,
        tz=UTC,
        reminders=[{"mode": "absolute", "hour": 9, "minute": 0}],
        repeat="monthly",
        now=now,
    )
    assert next_occurrence.astimezone(UTC).date().isoformat() == "2026-07-15"
    assert fire is not None and fire > now
