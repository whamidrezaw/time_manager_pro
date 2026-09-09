#!/usr/bin/env python3
"""
apply_batch18.py — TimeManager Pro, Batch 18 (punctuality and health)

Two things, and the first explains the second.

Reminders arrive late — half an hour, sometimes several hours. The cause is
not in this codebase: the worker is triggered by a GitHub Actions `schedule`,
and GitHub documents that scheduled workflows are delayed under load and may
be dropped entirely. A five-minute cron there is a suggestion, not a promise.

So the worker gets a second, punctual trigger: an HTTP endpoint that any
external cron can call every minute. The Action stays as a fallback — the
existing claim-before-send makes a double trigger harmless.

The second thing is how you would have found that out on your own: a health
check that measures lateness rather than errors, and tells you when reminders
are sitting past their time.

Run once from the repository root:

    python apply_batch18.py --check   # dry run, writes nothing
    python apply_batch18.py           # apply

Requires Batch 14. Safe to run twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRY_RUN = "--check" in sys.argv

PENDING: dict[Path, str] = {}
LOG: list[tuple[str, str]] = []
FAILED = False


def _note(status: str, message: str) -> None:
    LOG.append((status, message))


def _fail(message: str) -> None:
    global FAILED
    FAILED = True
    _note("FAIL", message)


def _current(path: Path) -> str | None:
    if path in PENDING:
        return PENDING[path]
    return path.read_text(encoding="utf-8") if path.exists() else None


def patch(rel: str, old: str, new: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found — are you in the repository root?")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    count = text.count(old)
    if count != 1:
        _fail(f"{rel}: anchor for '{label}' matched {count} times, expected 1 "
              "— is Batch 14 applied?")
        return

    PENDING[path] = text.replace(old, new, 1)
    _note(" OK ", f"{rel}: {label}")


def append(rel: str, addition: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    PENDING[path] = text.rstrip("\n") + "\n" + addition
    _note(" OK ", f"{rel}: {label}")


def create(rel: str, content: str, label: str) -> None:
    path = ROOT / rel
    PENDING[path] = content
    _note(" OK " if not path.exists() else "OVER", f"{rel}: {label}")


def flush() -> None:
    for path, content in PENDING.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 1. Settings
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/config.py",
    old="""    reminder_poll_interval_secs: int = Field(default=30, alias="REMINDER_POLL_INTERVAL_SECS")
""",
    new="""    reminder_poll_interval_secs: int = Field(default=30, alias="REMINDER_POLL_INTERVAL_SECS")

    # Batch 18. The shared secret an external cron sends to trigger a run.
    # Empty means the endpoint stays closed, so a fresh deployment cannot be
    # poked by anyone who guesses the path.
    tasks_secret: str = Field(default="", alias="TASKS_SECRET")
    # Where the health report goes. Your own Telegram user id.
    admin_chat_id: str = Field(default="", alias="ADMIN_CHAT_ID")
    # How late a pending reminder has to be before it counts as overdue.
    overdue_after_minutes: int = Field(default=10, alias="OVERDUE_AFTER_MINUTES")
""",
    marker="tasks_secret",
    label="task and health settings",
)

patch(
    ".env.example",
    old="""REMINDER_POLL_INTERVAL_SECS=30
""",
    new="""REMINDER_POLL_INTERVAL_SECS=30

# ==================== SCHEDULING & HEALTH (Batch 18) ====================
# A long random string. An external cron sends it as X-Tasks-Secret to
# trigger a reminder run on time; leave it empty to keep the endpoint closed.
TASKS_SECRET=
# Your own Telegram user id — the health report is sent there.
ADMIN_CHAT_ID=
# A pending reminder later than this many minutes counts as overdue.
OVERDUE_AFTER_MINUTES=10
""",
    marker="TASKS_SECRET",
    label="document the new settings",
)

# ══════════════════════════════════════════════════════════════════════
# 2. The health service
# ══════════════════════════════════════════════════════════════════════

HEALTH = '''from __future__ import annotations

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
    return "\\n".join(lines)


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
'''

create("app/services/health.py", HEALTH, "the health check")

# ══════════════════════════════════════════════════════════════════════
# 3. A punctual trigger
# ══════════════════════════════════════════════════════════════════════

TASKS = '''from __future__ import annotations

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
'''

create("app/routes/tasks.py", TASKS, "the task endpoints")

patch(
    "app/main.py",
    old="from app.routes.sharegroup import router as sharegroup_router\n",
    new=(
        "from app.routes.sharegroup import router as sharegroup_router\n"
        "from app.routes.tasks import router as tasks_router\n"
    ),
    marker="tasks_router",
    label="import the tasks router",
)

patch(
    "app/main.py",
    old="app.include_router(share_router)\n",
    new=(
        "app.include_router(share_router)\n"
        "app.include_router(tasks_router)\n"
    ),
    marker="include_router(tasks_router)",
    label="register the tasks router",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The debugging guide
# ══════════════════════════════════════════════════════════════════════

DEBUGGING = '''# Debugging TimeManager Pro

A prompt and a procedure. Paste the top section into an AI assistant together
with your symptom; work through the rest yourself.

---

## The prompt

> You are helping me debug **TimeManager Pro**, a Telegram Mini App and bot.
> Read this description before proposing anything.
>
> **Shape of the system.** A FastAPI app on Render serves both the Telegram
> webhook and the Mini App (`/webapp`). MongoDB Atlas holds `events`, `users`
> and `chats`. Reminders are sent by `process_due_reminders`, triggered two
> ways: `POST /tasks/run-reminders` from an external cron, and a GitHub
> Actions schedule as a fallback. Front end is vanilla JavaScript with no
> build step: `app.js` plus `referral.js`, `share.js`, `views.js` and
> `invite.js`, which talk to it through `window.TMApp` and `window.TMShare`.
>
> **Rules I hold you to.**
> 1. Do not propose a fix before you can name the mechanism. "Try adding a
>    delay" is not a diagnosis.
> 2. Occurrence and reminder maths lives in `app/utils/dates.py` and nowhere
>    else. If a second implementation would fix it, the fix is wrong.
> 3. Every event belongs to a `user_id`; every query that touches one event
>    filters on `{_id, user_id}` together. A fix that drops that is a data
>    leak, not a fix.
> 4. Timezones: events store `date_iso` plus `tz_name`, and everything is
>    computed in the event's own zone, never the server's. `datetime.now()`
>    without a timezone is a bug.
> 5. The front end has no build step and no framework. Do not introduce one.
> 6. Changes arrive as a single self-applying Python script with exact
>    anchors, a `--check` mode, and no writes unless every step resolves.
> 7. Tell me what you are unsure about. A confident wrong cause costs more
>    than an honest "I need to see X".
>
> **My symptom:** …
>
> **What I have already checked:** …

---

## Before you debug anything

Reproduce it, and write down the smallest input that shows it. Half the
reports in this project turned out to be two different behaviours wearing the
same description — "the repeat is broken" meant *repeating reminders before
the event*, which the app had never had.

## Where things break, in order of likelihood

**1. The reminder never arrived.**
Check punctuality first, not the code:

```bash
curl -s -X POST -H "X-Tasks-Secret: $TASKS_SECRET" https://YOUR_HOST/tasks/health
```

`overdue: 0` means the queue is moving and the problem is one event, not the
worker. A non-zero `overdue` with a large `worst_late_minutes` means the
trigger is not firing — check the cron, then the GitHub Action.

Then look at the event itself: `notify_status` should be `pending` with a
`next_notify_at` in the future. `done` with a repeat set means the series
ended; `processing` for more than fifteen minutes means a run died mid-send
and `recover_stale_processing` is not running.

**2. The reminder arrived at the wrong time.**
Almost always a timezone. Compare `tz_name` on the event with the timezone
the browser reported when it was created. `first_schedule` computes in the
event's zone; if the zone is wrong, everything downstream is consistently
wrong by the same offset — which is the tell.

**3. The Mini App shows stale content.**
`asset_version` is a hash of the static files computed at boot. If Render has
not redeployed, the old hash is still being served and the browser is right
to use its cache. Check that the new code is actually live:

```bash
curl -s https://YOUR_HOST/static/app.js | grep -c "SOMETHING_FROM_YOUR_CHANGE"
```

**4. The calendar shows the wrong days.**
`expand_occurrences` walks the series with `advance_occurrence` from
`app/utils/dates.py`. If the calendar and the reminders disagree, one of them
stopped using that function — that is the bug, not the dates.

**5. Something works for you and not for another account.**
Ownership. Every read of a single event filters on `user_id` as well as
`_id`. A shared event has a member copy per user; check `share_role` before
concluding anything about "the" event.

**6. A deep link does nothing.**
Four kinds share one entry point: `r_` referral, `e_` public countdown,
`s_` shared event, `g_` chat destination. Each parser rejects the other
three by design. A link that silently does nothing is usually the right
prefix handled by the wrong file.

## Tools

```bash
ruff check . && pytest              # 229 tests; run before you look anywhere else
python -m pytest tests/test_dates.py -q     # the maths, in isolation
curl -s -X POST -H "X-Tasks-Secret: $TASKS_SECRET" https://YOUR_HOST/tasks/run-reminders
```

Render's dashboard has the live logs. Every module logs under `tm_pro.*`, so
`tm_pro.reminders`, `tm_pro.cards`, `tm_pro.share` and `tm_pro.chats` narrow
a search quickly.

## The rule that has paid off most

When a symptom and an explanation both sound right, find the one measurement
that separates them before writing any code. The reminders being late looked
like a bug in the scheduling maths for weeks; one look at the *run history*
rather than the code showed the runs themselves were arriving late.
'''

create("docs/DEBUGGING.md", DEBUGGING, "the debugging guide")

# ══════════════════════════════════════════════════════════════════════
# 5. README
# ══════════════════════════════════════════════════════════════════════

patch(
    "README.md",
    old="""| `WEBAPP_BASE_URL` | Also the origin of public countdown links (`/c/<token>`) and share cards |
""",
    new="""| `WEBAPP_BASE_URL` | Also the origin of public countdown links (`/c/<token>`) and share cards |
| `TASKS_SECRET` | Shared secret for `POST /tasks/run-reminders`; empty keeps the endpoint closed |
| `ADMIN_CHAT_ID` | Your Telegram user id — where the health report is sent |
| `OVERDUE_AFTER_MINUTES` | How late a pending reminder may be before it is reported |
""",
    marker="TASKS_SECRET",
    label="document the settings",
)

patch(
    "README.md",
    old="""## Features
""",
    new="""## Keeping reminders punctual

The reminder worker has two triggers. The GitHub Actions schedule is the
fallback; **it is not punctual** — GitHub delays scheduled workflows under
load and drops them outright, which shows up as reminders arriving anywhere
from ten minutes to several hours late.

For reminders that arrive on the minute, point any external cron at the app
once a minute:

```
POST https://<your host>/tasks/run-reminders
Header: X-Tasks-Secret: <TASKS_SECRET>
```

Both triggers can run together. Each event is claimed with an atomic status
change before anything is sent, so whichever arrives second finds nothing to
do. A second job, once a day, sends you a summary:

```
POST https://<your host>/tasks/health
```

It reports how many reminders are overdue and how late the worst one is —
lateness, not errors, because a worker that never runs raises nothing.

## Features
""",
    marker="Keeping reminders punctual",
    label="the punctuality section",
)

patch(
    "README.md",
    old="""- **Dual calendar.** Every event carries both its Gregorian and its Jalali date.
""",
    new="""- **Dual calendar.** Every event carries both its Gregorian and its Jalali date.
- **Reminders that lead up to the event.** Set an event two months out and be
  nudged daily, weekly or monthly until the day arrives — then the series stops.
- **Month view and a year grid.** The month grid marks today by shape as well as
  colour; the strip above it shows the whole year by event density.
- **Shared events.** Link one event across several people. Each keeps their own
  copy, so each keeps their own timezone, reminder time, note and checklist,
  while the title and the date stay in step.
- **Group and channel reminders.** Add the bot to a group or channel and a
  reminder goes there as well as to you.
- **Share cards.** A rendered image of any event, with a public countdown page
  behind it, so a shared link previews as the event rather than as a bot.
- **A checklist on every event**, beside the note.
- **Invites that raise your limit.** Every three friends who join and save their
  first event add twenty events to your allowance.
""",
    marker="Reminders that lead up to the event",
    label="the feature list",
)

# ══════════════════════════════════════════════════════════════════════
# 6. Tests
# ══════════════════════════════════════════════════════════════════════

TESTS = '''from __future__ import annotations

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
'''

create("tests/test_health.py", TESTS, "health report tests")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 18 (punctuality and health)\n")
    print("  " + "─" * min(width + 8, 76))
    for status, message in LOG:
        print(f"  [{status}] {message}")
    print("  " + "─" * min(width + 8, 76))

    if FAILED:
        print("\n  Nothing was written. Fix the files named above and run again.\n")
        return 1

    if DRY_RUN:
        print(f"\n  Dry run: {len(PENDING)} file(s) would change. Nothing written.\n")
        return 0

    flush()
    print(f"\n  {len(PENDING)} file(s) written. Next:\n")
    print("      ruff check . && pytest")
    print("      git add -A && git commit -m 'Batch 18: punctual reminders and a health report'")
    print()
    print("  Then set TASKS_SECRET and ADMIN_CHAT_ID on Render and point a cron")
    print("  at /tasks/run-reminders every minute. Until you do, nothing changes.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
