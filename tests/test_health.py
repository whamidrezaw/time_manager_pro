from __future__ import annotations

from datetime import datetime, timezone

from app.services.health import format_report

BASE = {
    "checked_at": datetime(2026, 9, 9, 8, 30, tzinfo=timezone.utc),
    "overdue": 0,
    "worst_late_minutes": 0,
    "stuck": 0,
    "due_next_24h": 4,
    "touched_last_24h": 11,
    "healthy": True,
}


def test_a_healthy_report_says_so_first() -> None:
    text = format_report(BASE)

    assert text.startswith("✅")
    assert "on time" in text


def test_an_unhealthy_report_leads_with_the_warning() -> None:
    text = format_report({**BASE, "overdue": 3, "worst_late_minutes": 95, "healthy": False})

    assert text.startswith("⚠️")
    assert "3" in text
    assert "95" in text


def test_the_lateness_line_only_appears_when_something_is_late() -> None:
    """A healthy report that still mentioned lateness would train you to
    ignore the one that matters."""
    assert "late" not in format_report(BASE)


def test_stuck_events_are_called_out_separately() -> None:
    """Overdue means nothing picked it up; stuck means something died holding
    it. They have different causes and different fixes."""
    text = format_report({**BASE, "stuck": 2, "healthy": False})

    assert "Stuck" in text


def test_the_report_always_carries_its_timestamp() -> None:
    assert "2026-09-09 08:30" in format_report(BASE)
