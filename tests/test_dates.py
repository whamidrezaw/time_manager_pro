from __future__ import annotations

from datetime import datetime, timezone

from app.utils.dates import (
    build_event_datetimes,
    calc_next_notify,
    expire_for_repeat,
    safe_zoneinfo,
    to_jalali,
)


def test_safe_zoneinfo_returns_valid_zone() -> None:
    tz, name = safe_zoneinfo("Asia/Tehran")
    assert name == "Asia/Tehran"
    assert getattr(tz, "key", None) == "Asia/Tehran"


def test_safe_zoneinfo_falls_back_to_utc() -> None:
    tz, name = safe_zoneinfo("Invalid/Timezone")
    assert name == "UTC"
    assert getattr(tz, "key", None) == "UTC"


def test_to_jalali_returns_formatted_value() -> None:
    result = to_jalali("2025-03-21")
    assert isinstance(result, str)
    assert len(result) == 10
    assert "/" in result


def test_to_jalali_fallbacks_for_invalid_input() -> None:
    assert to_jalali("not-a-date") == "not-a-date"


def test_build_event_datetimes_returns_utc_datetimes() -> None:
    tz, _ = safe_zoneinfo("UTC")
    notify_utc, event_utc = build_event_datetimes("2026-04-20", tz, reminder_hour=9)

    assert notify_utc.tzinfo == timezone.utc
    assert event_utc.tzinfo == timezone.utc
    assert notify_utc.hour == 9
    assert event_utc.hour == 0


def test_expire_for_repeat_none_is_future() -> None:
    anchor = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
    result = expire_for_repeat(anchor, "none")
    assert result > anchor


def test_calc_next_notify_returns_none_for_non_repeating_event() -> None:
    tz, _ = safe_zoneinfo("UTC")
    base_date = datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)

    assert calc_next_notify(base_date, "none", tz, reminder_hour=9) is None


def test_calc_next_notify_for_daily_returns_future_time() -> None:
    tz, _ = safe_zoneinfo("UTC")
    base_date = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)

    result = calc_next_notify(base_date, "daily", tz, reminder_hour=9)

    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result > datetime.now(timezone.utc)


def test_calc_next_notify_for_monthly_returns_future_time() -> None:
    tz, _ = safe_zoneinfo("UTC")
    base_date = datetime(2024, 1, 31, 0, 0, tzinfo=timezone.utc)

    result = calc_next_notify(base_date, "monthly", tz, reminder_hour=9)

    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result > datetime.now(timezone.utc)
