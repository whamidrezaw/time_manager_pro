from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jdatetime

logger = logging.getLogger("tm_pro.dates")

REPEAT_VALUES = {"none", "daily", "weekly", "monthly", "yearly"}

# A reminder may sit at most 30 days ahead of its event.
MAX_OFFSET_MINUTES = 60 * 24 * 30
# Enough to walk a daily event roughly 11 years forward before giving up.
MAX_SCHEDULE_STEPS = 4000

DEFAULT_REMINDER_HOUR = 9
DEFAULT_REMINDER_MINUTE = 0


def safe_zoneinfo(tz_name: str | None) -> tuple[ZoneInfo, str]:
    try:
        if tz_name:
            return ZoneInfo(tz_name), tz_name
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("Invalid timezone received: %s", tz_name)

    return ZoneInfo("UTC"), "UTC"


def to_jalali(date_iso: str) -> str:
    try:
        parsed = datetime.strptime(date_iso, "%Y-%m-%d")
        jalali_date = jdatetime.date.fromgregorian(date=parsed.date())
        return jalali_date.strftime("%Y/%m/%d")
    except Exception:
        return date_iso


def expire_for_repeat(anchor: datetime, repeat: str) -> datetime:
    delta_map = {
        "none": timedelta(days=30),
        "daily": timedelta(days=2),
        "weekly": timedelta(days=10),
        "monthly": timedelta(days=40),
        "yearly": timedelta(days=400),
    }
    return anchor + delta_map.get(repeat, timedelta(days=30))


def repeat_label(repeat: str) -> str:
    return {
        "none": "One-time",
        "daily": "🔁 Daily",
        "weekly": "🔁 Weekly",
        "monthly": "🔁 Monthly",
        "yearly": "🎂 Yearly",
    }.get(repeat, "One-time")


# ── Time of day ─────────────────────────────────────────────────────────────

def parse_time_hm(value: str) -> tuple[int, int]:
    """'14:30' -> (14, 30). Raises ValueError on anything else."""
    hour_str, separator, minute_str = (value or "").strip().partition(":")
    if not separator:
        raise ValueError("time must look like HH:MM")

    hour, minute = int(hour_str), int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time out of range")

    return hour, minute


def format_time_hm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


# ── Occurrences ─────────────────────────────────────────────────────────────

def build_occurrence(
    date_str: str,
    tz: ZoneInfo,
    all_day: bool = True,
    time_hm: str | None = None,
) -> datetime:
    """The instant the event itself starts, in UTC.

    All-day events anchor at local midnight, which is what the day countdown
    and the list ordering expect. A timed event anchors at its own local time.
    """
    base = datetime.strptime(date_str, "%Y-%m-%d")

    hour, minute = 0, 0
    if not all_day and time_hm:
        hour, minute = parse_time_hm(time_hm)

    local = base.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)
    return local.astimezone(timezone.utc)


def shift_month(reference: datetime, anchor_day: int, months_ahead: int = 1) -> datetime:
    """Move whole months, re-anchoring on the original day of the month.

    Anchoring on `anchor_day` rather than on the previous result is what stops
    a monthly event on the 31st from landing on the 28th after one February
    and then staying there for good.
    """
    month = reference.month - 1 + months_ahead
    year = reference.year + month // 12
    month = month % 12 + 1
    day = min(anchor_day, monthrange(year, month)[1])

    return reference.replace(year=year, month=month, day=day, second=0, microsecond=0)


def advance_occurrence(
    occurrence_utc: datetime,
    repeat: str,
    tz: ZoneInfo,
    anchor_day: int,
) -> datetime | None:
    """One repeat period forward.

    Steps on the wall clock rather than on absolute time, so an event at 09:00
    stays at 09:00 across a daylight-saving change instead of drifting to 08:00.
    """
    if repeat == "none":
        return None

    local = occurrence_utc.astimezone(tz).replace(tzinfo=None)

    if repeat == "daily":
        nxt = local + timedelta(days=1)
    elif repeat == "weekly":
        nxt = local + timedelta(weeks=1)
    elif repeat == "monthly":
        nxt = shift_month(local, anchor_day, 1)
    elif repeat == "yearly":
        try:
            nxt = local.replace(year=local.year + 1)
        except ValueError:
            # 29 February in a non-leap year.
            nxt = local.replace(year=local.year + 1, month=3, day=1)
    else:
        return None

    return nxt.replace(tzinfo=tz).astimezone(timezone.utc)


# ── Reminders ───────────────────────────────────────────────────────────────

def normalize_reminders(
    raw: object,
    all_day: bool = True,
    legacy_hour: int = DEFAULT_REMINDER_HOUR,
    legacy_minute: int = DEFAULT_REMINDER_MINUTE,
) -> list[dict]:
    """Return the reminder list in canonical form.

    Accepts the stored list as well as the older reminder_hour / reminder_minute
    pair, so documents written before this field existed keep working with no
    migration step.

    An absolute reminder fires at a wall-clock time on the day of the event; a
    relative one fires a fixed number of minutes before it starts. All-day
    events only take absolute reminders, because "15 minutes before" has no
    meaning for a day that carries no time.
    """
    specs: list[dict] = []

    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("mode", "absolute")).lower() == "relative":
            offset = int(item.get("offset_minutes", 0) or 0)
            specs.append({
                "mode": "relative",
                "offset_minutes": max(0, min(offset, MAX_OFFSET_MINUTES)),
            })
        else:
            specs.append({
                "mode": "absolute",
                "hour": max(0, min(int(item.get("hour", legacy_hour) or 0), 23)),
                "minute": max(0, min(int(item.get("minute", legacy_minute) or 0), 59)),
            })

    if all_day:
        specs = [spec for spec in specs if spec["mode"] == "absolute"]

    if not specs:
        specs = [{
            "mode": "absolute",
            "hour": max(0, min(int(legacy_hour), 23)),
            "minute": max(0, min(int(legacy_minute), 59)),
        }]

    return specs


def reminder_fire_time(spec: dict, occurrence_utc: datetime, tz: ZoneInfo) -> datetime:
    """When a single reminder fires, in UTC."""
    if spec.get("mode") == "relative":
        return occurrence_utc - timedelta(minutes=int(spec.get("offset_minutes", 0)))

    local = occurrence_utc.astimezone(tz).replace(
        hour=int(spec.get("hour", DEFAULT_REMINDER_HOUR)),
        minute=int(spec.get("minute", DEFAULT_REMINDER_MINUTE)),
        second=0,
        microsecond=0,
    )
    return local.astimezone(timezone.utc)


def earliest_fire(
    reminders: list[dict],
    occurrence_utc: datetime,
    tz: ZoneInfo,
    after: datetime | None = None,
) -> datetime | None:
    """Earliest reminder for this occurrence, optionally only ones still ahead."""
    times = [reminder_fire_time(spec, occurrence_utc, tz) for spec in reminders]
    if after is not None:
        times = [moment for moment in times if moment > after]

    return min(times) if times else None


# ── Scheduling ──────────────────────────────────────────────────────────────

def _past_until(occurrence_utc: datetime, tz: ZoneInfo, repeat_until: str | None) -> bool:
    if not repeat_until:
        return False
    return occurrence_utc.astimezone(tz).date().isoformat() > repeat_until


def next_schedule(
    *,
    occurrence_utc: datetime,
    anchor_day: int,
    tz: ZoneInfo,
    reminders: list[dict],
    repeat: str,
    repeat_until: str | None = None,
    now: datetime | None = None,
    max_steps: int = MAX_SCHEDULE_STEPS,
) -> tuple[datetime, datetime | None]:
    """Walk forward to the next occurrence that still has a reminder ahead of it.

    Returns (occurrence, fire_time). A fire_time of None means the series is
    finished: either it does not repeat, or it ran past repeat_until.
    """
    now = now or datetime.now(timezone.utc)
    occurrence = occurrence_utc

    for _ in range(max_steps):
        nxt = advance_occurrence(occurrence, repeat, tz, anchor_day)
        if nxt is None:
            return occurrence, None

        occurrence = nxt
        if _past_until(occurrence, tz, repeat_until):
            return occurrence, None

        fire = earliest_fire(reminders, occurrence, tz, after=now)
        if fire is not None:
            return occurrence, fire

    logger.error("next_schedule exceeded %s steps (repeat=%s)", max_steps, repeat)
    return occurrence, None


def first_schedule(
    *,
    date_str: str,
    tz: ZoneInfo,
    all_day: bool,
    time_hm: str | None,
    reminders: list[dict],
    repeat: str,
    repeat_until: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime | None]:
    """Occurrence and next reminder time for an event that was just saved."""
    now = now or datetime.now(timezone.utc)
    occurrence = build_occurrence(date_str, tz, all_day, time_hm)

    if repeat == "none":
        # Unchanged behaviour for one-off events: if the reminder time has
        # already passed, it still fires on the next worker pass rather than
        # being dropped silently.
        return occurrence, earliest_fire(reminders, occurrence, tz)

    if not _past_until(occurrence, tz, repeat_until):
        fire = earliest_fire(reminders, occurrence, tz, after=now)
        if fire is not None:
            return occurrence, fire

    return next_schedule(
        occurrence_utc=occurrence,
        anchor_day=int(date_str[8:10]),
        tz=tz,
        reminders=reminders,
        repeat=repeat,
        repeat_until=repeat_until,
        now=now,
    )
