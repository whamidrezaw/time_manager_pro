from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.utils.occurrences import expand_occurrences


def _event(**overrides) -> dict:
    base = {
        "date_iso": "2026-03-10",
        "tz_name": "Europe/Berlin",
        "all_day": True,
        "repeat": "none",
    }
    base.update(overrides)
    return base


YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


def test_a_one_off_event_appears_once_and_only_in_range() -> None:
    event = _event()

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [date(2026, 3, 10)]
    assert expand_occurrences(event, date(2026, 4, 1), YEAR_END) == []


def test_a_yearly_birthday_draws_one_dot_a_year() -> None:
    """The stored event knows one occurrence; a calendar needs the series."""
    event = _event(date_iso="1998-07-20", repeat="yearly")

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [date(2026, 7, 20)]


def test_a_monthly_event_draws_twelve() -> None:
    found = expand_occurrences(_event(date_iso="2026-01-15", repeat="monthly"),
                               YEAR_START, YEAR_END)

    assert len(found) == 12
    assert found[0] == date(2026, 1, 15)
    assert found[-1] == date(2026, 12, 15)


def test_a_monthly_event_on_the_31st_comes_back_after_a_short_month() -> None:
    """Re-anchoring is advance_occurrence's job; this proves we did not lose it."""
    found = expand_occurrences(_event(date_iso="2026-01-31", repeat="monthly"),
                               date(2026, 1, 1), date(2026, 4, 30))

    assert found == [date(2026, 1, 31), date(2026, 2, 28),
                     date(2026, 3, 31), date(2026, 4, 30)]


def test_a_weekly_event_lands_on_the_same_weekday_every_time() -> None:
    found = expand_occurrences(_event(date_iso="2026-09-07", repeat="weekly"),
                               date(2026, 9, 1), date(2026, 9, 30))

    assert found == [date(2026, 9, 7), date(2026, 9, 14),
                     date(2026, 9, 21), date(2026, 9, 28)]


def test_a_daily_event_that_started_years_ago_still_fills_the_window() -> None:
    """Fast-forwarding must land on the series, not next to it."""
    found = expand_occurrences(_event(date_iso="2019-01-01", repeat="daily"),
                               date(2026, 9, 1), date(2026, 9, 5))

    assert found == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
                     date(2026, 9, 4), date(2026, 9, 5)]


def test_repeat_until_ends_the_series() -> None:
    found = expand_occurrences(
        _event(date_iso="2026-01-15", repeat="monthly", repeat_until="2026-04-30"),
        YEAR_START, YEAR_END,
    )

    assert found == [date(2026, 1, 15), date(2026, 2, 15),
                     date(2026, 3, 15), date(2026, 4, 15)]


def test_the_step_cap_holds() -> None:
    """A daily series over a decade must return, not spin."""
    found = expand_occurrences(_event(date_iso="2026-01-01", repeat="daily"),
                               YEAR_START, date(2036, 1, 1), max_steps=10)

    assert len(found) == 10


@pytest.mark.parametrize("bad", [{}, {"date_iso": ""}, {"date_iso": "not-a-date"}])
def test_a_broken_event_yields_nothing_instead_of_raising(bad) -> None:
    """One malformed document must not take the whole calendar down."""
    assert expand_occurrences(bad, YEAR_START, YEAR_END) == []


def test_an_inverted_range_is_empty() -> None:
    assert expand_occurrences(_event(), YEAR_END, YEAR_START) == []


def test_a_finished_series_stops_at_its_last_occurrence() -> None:
    """The reported bug: a weekly event kept drawing dots after it was over.

    A repeat rule has no end of its own, so replaying it forward invents
    occurrences the event will never have. notify_status "done" plus the
    stored event_ts_utc is where the worker left the series.
    """
    event = _event(
        date_iso="2026-01-05",
        repeat="weekly",
        notify_status="done",
        event_ts_utc=datetime(2026, 2, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert expand_occurrences(event, YEAR_START, YEAR_END) == [
        date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19),
        date(2026, 1, 26), date(2026, 2, 2),
    ]


def test_a_running_series_is_untouched_by_that_rule() -> None:
    event = _event(
        date_iso="2026-01-05",
        repeat="weekly",
        notify_status="pending",
        event_ts_utc=datetime(2026, 2, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert len(expand_occurrences(event, YEAR_START, YEAR_END)) > 5


def test_a_finished_series_that_ended_before_the_window_draws_nothing() -> None:
    event = _event(
        date_iso="2024-01-05",
        repeat="weekly",
        notify_status="done",
        event_ts_utc=datetime(2024, 3, 1, 8, 0, tzinfo=timezone.utc),
    )

    assert expand_occurrences(event, YEAR_START, YEAR_END) == []
