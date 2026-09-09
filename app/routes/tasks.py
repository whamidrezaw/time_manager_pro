from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException
from telegram import Bot

from app.config import get_settings
from app.services.health import measure, send_report
from app.services.reminders import process_due_reminders, recover_stale_processing

router = APIRouter(tags=["tasks"])
logger = logging.getLogger("tm_pro.tasks")


def _authorise(provided: str | None) -> None:
    """Constant-time comparison, and closed by default.

    An empty TASKS_SECRET does not mean "allow everyone", it means the
    endpoint is not in service — a deployment that forgot to set it should
    fail loudly rather than expose a way to make the bot send messages.
    """
    settings = get_settings()
    if not settings.tasks_secret:
        raise HTTPException(status_code=503, detail="TASKS_DISABLED")
    if not provided or not hmac.compare_digest(provided, settings.tasks_secret):
        raise HTTPException(status_code=403, detail="FORBIDDEN")


@router.post("/tasks/run-reminders")
async def run_reminders(x_tasks_secret: str | None = Header(default=None)) -> dict:
    """Do one pass of the reminder queue.

    This exists because the GitHub Actions schedule that used to be the only
    trigger is not punctual: GitHub delays scheduled workflows under load and
    drops them outright, which is why reminders were arriving an hour late.
    An external cron calling this every minute is accurate to the minute.

    Running alongside the Action is safe: process_due_reminders claims each
    event with an atomic status change before sending, so whichever trigger
    arrives second finds nothing left to do.
    """
    _authorise(x_tasks_secret)
    settings = get_settings()

    recovered = await recover_stale_processing(settings)

    async with Bot(token=settings.bot_token) as bot:
        processed = await process_due_reminders(bot, settings)

    stats = await measure(settings)
    if not stats["healthy"]:
        # Reported the moment it is noticed rather than in tomorrow's summary:
        # a reminder that is late is only useful if it is fixed today.
        await send_report(settings, only_if_unhealthy=True)

    logger.info("task run processed=%s recovered=%s overdue=%s",
                processed, recovered, stats["overdue"])
    return {"success": True, "processed": processed, "recovered": recovered,
            "overdue": stats["overdue"]}


@router.post("/tasks/health")
async def health_report(x_tasks_secret: str | None = Header(default=None)) -> dict:
    """The daily summary. Call it once a day from the same cron."""
    _authorise(x_tasks_secret)

    stats = await send_report(get_settings())
    return {"success": True, **{k: v for k, v in stats.items() if k != "checked_at"}}
