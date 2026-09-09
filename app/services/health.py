from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.db import get_events_collection

logger = logging.getLogger("tm_pro.health")


async def measure(settings: Settings | None = None) -> dict:
    """How punctual the reminders are right now.

    Deliberately measures lateness rather than errors. A worker that never
    runs throws no exception and logs nothing — it simply leaves reminders
    sitting in the queue past their time, which is invisible in a log file
    and obvious in this number. Counting overdue events is what would have
    caught the GitHub Actions delay on the first day instead of the hundredth.
    """
    settings = settings or get_settings()
    events = get_events_collection()
    now = datetime.now(timezone.utc)

    overdue_cutoff = now - timedelta(minutes=max(settings.overdue_after_minutes, 1))
    stuck_cutoff = now - timedelta(minutes=15)
    day_ago = now - timedelta(hours=24)

    overdue = await events.count_documents(
        {"notify_status": "pending", "next_notify_at": {"$lt": overdue_cutoff}}
    )
    # A row left in "processing" means a run claimed it and died before it
    # sent anything. recover_stale_processing puts those back, so a number
    # here means that recovery is not running either.
    stuck = await events.count_documents(
        {"notify_status": "processing", "processing_started_at": {"$lt": stuck_cutoff}}
    )
    upcoming = await events.count_documents(
        {"notify_status": "pending", "next_notify_at": {"$gte": now, "$lt": now + timedelta(hours=24)}}
    )
    sent_today = await events.count_documents(
        {"notify_status": {"$in": ["pending", "done"]}, "updated_at": {"$gte": day_ago}}
    )

    worst = await events.find_one(
        {"notify_status": "pending", "next_notify_at": {"$lt": overdue_cutoff}},
        {"next_notify_at": 1},
        sort=[("next_notify_at", 1)],
    )
    worst_minutes = 0
    if worst and worst.get("next_notify_at"):
        due = worst["next_notify_at"]
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        worst_minutes = int((now - due).total_seconds() // 60)

    return {
        "checked_at": now,
        "overdue": overdue,
        "worst_late_minutes": worst_minutes,
        "stuck": stuck,
        "due_next_24h": upcoming,
        "touched_last_24h": sent_today,
        "healthy": overdue == 0 and stuck == 0,
    }


def format_report(stats: dict) -> str:
    """The message that lands in the admin chat."""
    head = "✅ <b>Reminders are on time</b>" if stats["healthy"] else "⚠️ <b>Reminders are running late</b>"

    lines = [
        head,
        "",
        f"Overdue right now: <b>{stats['overdue']}</b>",
    ]
    if stats["overdue"]:
        lines.append(f"Oldest one is <b>{stats['worst_late_minutes']} min</b> late")
    if stats["stuck"]:
        lines.append(f"Stuck mid-send: <b>{stats['stuck']}</b>")

    lines += [
        f"Due in the next 24h: <b>{stats['due_next_24h']}</b>",
        "",
        f"<i>{stats['checked_at'].strftime('%Y-%m-%d %H:%M')} UTC</i>",
    ]
    return "\n".join(lines)


async def send_report(settings: Settings | None = None, only_if_unhealthy: bool = False) -> dict:
    """Measure, and tell the admin. Returns the numbers either way."""
    settings = settings or get_settings()
    stats = await measure(settings)

    if not settings.admin_chat_id:
        return stats
    if only_if_unhealthy and stats["healthy"]:
        return stats

    try:
        from telegram import Bot

        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=settings.admin_chat_id,
                text=format_report(stats),
                parse_mode="HTML",
            )
    except Exception:
        # The health report failing must never be the thing that takes the
        # scheduler down with it.
        logger.exception("health report could not be delivered")

    return stats
