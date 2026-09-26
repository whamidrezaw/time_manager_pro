"""Alerts to the admin, by Telegram, and the healthchecks.io ping (ADR 0014).

As chosen in Stage 3b: an alert for late or stuck reminders, for failures in
the last 24 hours, and for any failed reminder at all. It is sent when a
problem starts, repeated every six hours while it lasts, and followed by a
message when it is over. The state lives in the database and changes only by
atomic updates, so the cron endpoint and the Action, which can run at the same
moment, send each message once. Nothing here may raise into a reminder run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from telegram import Bot

from app.config import Settings, get_settings
from app.db import get_database

logger = logging.getLogger("tm_pro.alerts")

REPEAT_AFTER = timedelta(hours=6)
STATE_ID = "reminders"
TITLES = {
    "started": "🚨 <b>Reminder alert</b>",
    "repeated": "🚨 <b>Reminder alert, still unresolved</b>",
    "recovered": "✅ <b>Reminders are back to normal</b>",
    "test": "🔔 <b>Test alert</b>: the alert path works",
}


def reasons(stats: dict) -> list[str]:
    """What is wrong, by the reasons chosen to alert on."""
    return [name for key, name in (("overdue", "late"), ("stuck", "stuck"),
                                   ("failed_last_24h", "failed_24h"), ("failed", "failed"))
            if stats.get(key)]


def _duration(minutes: int) -> str:
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{days} d" if days else "", f"{hours} h" if hours else "",
             f"{mins} min" if mins or not (days or hours) else ""]
    return " ".join(p for p in parts if p)


def alert_text(kind: str, stats: dict, since: datetime | None = None, now: datetime | None = None) -> str:
    lines = [TITLES[kind], ""]
    if kind in ("repeated", "recovered") and since and now:
        label = "Unresolved for" if kind == "repeated" else "It lasted"
        lines.append(f"{label} <b>{_duration(int((now - since).total_seconds() // 60))}</b>")
    if stats.get("overdue"):
        oldest = stats.get("worst_late_minutes", 0)
        lines.append(f"Late: <b>{stats['overdue']}</b>, the oldest <b>{oldest} min</b>")
    if stats.get("stuck"):
        lines.append(f"Stuck mid-send: <b>{stats['stuck']}</b>")
    if stats.get("failed_last_24h"):
        lines.append(f"Failed in the last 24 h: <b>{stats['failed_last_24h']}</b>")
    if stats.get("failed"):
        lines.append(f"Failed, all time: <b>{stats['failed']}</b>")
    if kind != "recovered":
        lines += ["", "What to check: docs/RUNBOOK.md"]
    return "\n".join(lines)


async def send_alert(kind: str, stats: dict, settings: Settings | None = None,
                     since: datetime | None = None, now: datetime | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.admin_chat_id:
        return False
    try:
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(chat_id=settings.admin_chat_id, parse_mode="HTML",
                                   text=alert_text(kind, stats, since, now))
    except Exception:
        logger.exception("alert could not be delivered", extra={"event": "alert_failed", "kind": kind})
        return False
    logger.info("alert sent: %s", kind, extra={"event": "alert", "kind": kind, "reasons": reasons(stats)})
    return True


async def check_and_alert(stats: dict, settings: Settings | None = None,
                          now: datetime | None = None) -> str | None:
    """Move the alert state on and send what that move calls for, once."""
    settings = settings or get_settings()
    if not settings.admin_chat_id:
        return None
    now = now or datetime.now(timezone.utc)
    found = reasons(stats)
    try:
        state = get_database()["alert_state"]
        await state.update_one({"_id": STATE_ID}, {"$setOnInsert": {"active": False}}, upsert=True)
        if found:
            if await state.find_one_and_update(
                    {"_id": STATE_ID, "active": False},
                    {"$set": {"active": True, "since": now, "last_sent": now, "reasons": found}}):
                await send_alert("started", stats, settings)
                return "started"
            held = await state.find_one_and_update(
                {"_id": STATE_ID, "active": True, "last_sent": {"$lte": now - REPEAT_AFTER}},
                {"$set": {"last_sent": now, "reasons": found}})
            if held:
                await send_alert("repeated", stats, settings, since=held.get("since"), now=now)
                return "repeated"
            return None
        ended = await state.find_one_and_update(
            {"_id": STATE_ID, "active": True}, {"$set": {"active": False, "reasons": [], "ended": now}})
        if ended:
            await send_alert("recovered", stats, settings, since=ended.get("since"), now=now)
            return "recovered"
    except Exception:
        logger.exception("alert check failed", extra={"event": "alert_check_failed"})
    return None


async def _get(url: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        (await client.get(url)).raise_for_status()


async def ping_healthchecks(settings: Settings | None = None, fail: bool = False) -> bool:
    """Tell healthchecks.io the cron run happened (or failed); silence is its alarm."""
    settings = settings or get_settings()
    base = (getattr(settings, "healthcheck_ping_url", "") or "").rstrip("/")
    if not base:
        return False
    try:
        await _get(base + ("/fail" if fail else ""))
        return True
    except Exception:
        logger.warning("healthchecks ping failed", extra={"event": "healthchecks_ping_failed"})
        return False
