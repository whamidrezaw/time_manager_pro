from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app.utils.dates import (
    _past_until,
    advance_occurrence,
    build_occurrence,
    safe_zoneinfo,
)

logger = logging.getLogger("tm_pro.occurrences")

# A daily event across a full year is 365 of these; the cap is what stops a
# corrupt repeat value from turning one request into an endless loop.
MAX_STEPS = 800


def _fast_forward(occurrence: datetime, repeat: str, tz, start: date) -> datetime:
    """Skip the repeats that fall entirely before the window.

    Only daily and weekly get this. Both are plain wall-clock deltas, so
    jumping k periods at once lands exactly where k single steps would, and
    the calendar-sensitive cases — month lengths, 29 February — stay the sole
    responsibility of advance_occurrence.
    """
    days = {"daily": 1, "weekly": 7}.get(repeat)
    if not days:
        return occurrence

    local = occurrence.astimezone(tz).replace(tzinfo=None)
    behind = (start - local.date()).days
    if behind <= 0:
        return occurrence

    local += timedelta(days=(behind // days) * days)
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def expand_occurrences(event: dict, start: date, end: date,
                       max_steps: int = MAX_STEPS) -> list[date]:
    """Every day this event lands on between start and end, inclusive.

    The stored event only knows its next occurrence, which is enough for a
    reminder and not enough for a calendar: a monthly event would draw one dot
    a year. So the series is walked here — but every step is taken by
    advance_occurrence, the same function the reminder worker uses. A calendar
    that disagreed with the reminders would be worse than no calendar.

    (_past_until is private to app.utils.dates. It is imported rather than
    rewritten because "has this series ended" is exactly the kind of rule that
    must not exist twice.)
    """
    date_iso = str(event.get("date_iso") or "")
    if not date_iso or end < start:
        return []

    tz, _ = safe_zoneinfo(event.get("tz_name"))
    try:
        occurrence = build_occurrence(
            date_iso, tz, bool(event.get("all_day", True)), event.get("time_hm")
        )
    except ValueError:
        logger.warning("unparseable date_iso on event %s", event.get("_id"))
        return []

    repeat = str(event.get("repeat") or "none")
    if repeat == "none":
        only = occurrence.astimezone(tz).date()
        return [only] if start <= only <= end else []

    until = event.get("repeat_until")
    anchor_day = int(date_iso[8:10] or 1)

    # A repeat rule on its own never ends, so replaying it forward invents
    # occurrences the event will never actually have. The worker already
    # decided when the series was over — it wrote notify_status "done" and
    # left event_ts_utc on the last real occurrence — so the calendar stops
    # exactly where the reminders did instead of drawing dots into eternity.
    if str(event.get("notify_status")) == "done":
        last = event.get("event_ts_utc")
        if isinstance(last, datetime):
            end = min(end, last.astimezone(tz).date())
            if end < start:
                return []
    occurrence = _fast_forward(occurrence, repeat, tz, start)

    found: list[date] = []
    for _ in range(max_steps):
        local = occurrence.astimezone(tz).date()
        if local > end or _past_until(occurrence, tz, until):
            return found
        if local >= start:
            found.append(local)

        nxt = advance_occurrence(occurrence, repeat, tz, anchor_day)
        if nxt is None:
            return found
        occurrence = nxt

    logger.error("expand_occurrences hit the step cap (repeat=%s)", repeat)
    return found
