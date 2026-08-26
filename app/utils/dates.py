from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jdatetime

logger = logging.getLogger("tm_pro.dates")

REPEAT_VALUES = {"none", "daily", "weekly", "monthly", "yearly"}


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


def month_candidate(reference: datetime, anchor_day: int, months_ahead: int = 1) -> datetime:
    month = reference.month - 1 + months_ahead
    year = reference.year + month // 12
    month = month % 12 + 1
    day = min(anchor_day, monthrange(year, month)[1])

    return reference.replace(
        year=year,
        month=month,
        day=day,
        second=0,
        microsecond=0,
    )


def build_event_datetimes(
    date_str: str,
    tz: ZoneInfo,
    reminder_hour: int = 9,
    reminder_minute: int = 0,
) -> tuple[datetime, datetime]:
    local_dt = datetime.strptime(date_str, "%Y-%m-%d")
    notify_local = local_dt.replace(
        hour=reminder_hour,
        minute=reminder_minute,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )
    event_local = local_dt.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )
    return notify_local.astimezone(timezone.utc), event_local.astimezone(timezone.utc)


def calc_next_notify(
    base_date: datetime,
    repeat: str,
    tz: ZoneInfo,
    reminder_hour: int = 9,
    reminder_minute: int = 0,
) -> datetime | None:
    if repeat == "none":
        return None

    now_local = datetime.now(tz)
    candidate = base_date.astimezone(tz).replace(
        hour=reminder_hour,
        minute=reminder_minute,
        second=0,
        microsecond=0,
    )

    if repeat == "daily":
        # حداکثر ۳۶۵۰ بار = ۱۰ سال آینده کافیه
        for _ in range(3650):
            if candidate > now_local:
                break
            candidate += timedelta(days=1)
        else:
            logger.error("calc_next_notify daily loop exceeded max iterations for base=%s", base_date)
            return None

    elif repeat == "weekly":
        # حداکثر ۵۲۰ بار = ۱۰ سال آینده کافیه
        for _ in range(520):
            if candidate > now_local:
                break
            candidate += timedelta(weeks=1)
        else:
            logger.error("calc_next_notify weekly loop exceeded max iterations for base=%s", base_date)
            return None

    elif repeat == "monthly":
        # حداکثر ۱۵۰۰ بار = بیش از ۱۰۰ سال آینده کافیه
        for _ in range(1500):
            if candidate > now_local:
                break
            candidate = month_candidate(candidate, candidate.day, 1)
        else:
            logger.error("calc_next_notify monthly loop exceeded max iterations for base=%s", base_date)
            return None

    elif repeat == "yearly":
        # حداکثر ۲۰۰ بار = ۲۰۰ سال آینده کافیه
        for _ in range(200):
            if candidate > now_local:
                break
            try:
                candidate = candidate.replace(year=candidate.year + 1)
            except ValueError:
                # ۲۹ فوریه در سال کبیسه
                candidate = candidate.replace(year=candidate.year + 1, month=3, day=1)
        else:
            logger.error("calc_next_notify yearly loop exceeded max iterations for base=%s", base_date)
            return None

    else:
        return None

    return candidate.astimezone(timezone.utc)


def repeat_label(repeat: str) -> str:
    return {
        "none": "One-time",
        "daily": "🔁 Daily",
        "weekly": "🔁 Weekly",
        "monthly": "🔁 Monthly",
        "yearly": "🎂 Yearly",
    }.get(repeat, "One-time")
