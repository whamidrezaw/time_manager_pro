from __future__ import annotations

import hmac
import logging
import time

from fastapi import APIRouter, Header, HTTPException
from telegram import Bot

from app.config import get_settings
from app.observability import log_reminder_run
from app.services.alerts import check_and_alert, ping_healthchecks, send_alert
from app.services.health import measure, send_report
from app.services.reminders import process_due_reminders, recover_stale_processing
from app.services.telegram_bot import telegram_bot

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
    started = time.perf_counter()

    # Each phase timed, so the slow one is found, not guessed (Batch 30).
    phases: dict[str, float] = {}

    def lap(name: str, since: float) -> float:
        now = time.perf_counter()
        phases[name] = round((now - since) * 1000, 1)
        return now

    try:
        mark = time.perf_counter()
        recovered = await recover_stale_processing(settings)
        mark = lap("recover_ms", mark)
        async with telegram_bot(settings, Bot) as bot:
            mark = lap("bot_start_ms", mark)
            processed = await process_due_reminders(bot, settings)
            mark = lap("process_ms", mark)
        mark = lap("bot_stop_ms", mark)
        stats = await measure(settings)
        mark = lap("measure_ms", mark)
    except Exception:
        await ping_healthchecks(settings, fail=True)  # healthchecks.io hears of it at once
        raise

    # Once when a problem starts, every six hours while it lasts, once when it
    # ends (ADR 0014); the old path sent the whole report after every run.
    await check_and_alert(stats, settings)
    mark = lap("alert_ms", mark)
    await ping_healthchecks(settings)
    lap("ping_ms", mark)
    log_reminder_run(logger, "cron", processed, recovered, stats, started, phases)
    return {"success": True, "processed": processed, "recovered": recovered,
            "overdue": stats["overdue"]}


@router.post("/tasks/alert-test")
async def alert_test(x_tasks_secret: str | None = Header(default=None)) -> dict:
    """Fire the alert path once: a test message to the admin and one ping.

    docs/RUNBOOK.md asks for it after every deploy that touches alerting, so an
    alert that could not arrive is found on a quiet day, not during an outage.
    """
    _authorise(x_tasks_secret)
    settings = get_settings()
    return {"telegram": await send_alert("test", await measure(settings), settings),
            "healthchecks": await ping_healthchecks(settings)}


@router.post("/tasks/health")
async def health_report(x_tasks_secret: str | None = Header(default=None)) -> dict:
    """The daily summary. Call it once a day from the same cron."""
    _authorise(x_tasks_secret)

    stats = await send_report(get_settings())
    return {"success": True, **{k: v for k, v in stats.items() if k != "checked_at"}}
