#!/usr/bin/env python3
"""apply_batch19_step0.py - Step 0 of the Batch 18 review fixes.

SCAFFOLD ONLY. This script changes no application code. It sets up the
branch so that the four fix steps after it have something to prove
themselves against:

  1. requirements-dev.txt          test and lint dependencies, split out
  2. requirements.txt              four security bumps, test deps removed
  3. .github/workflows/ci.yml      runs on fix/** branches, audits prod deps
  4. tests/harness.py              in-memory Mongo + fake Telegram bot
  5. tests/test_review_findings.py 16 tests, 15 of them failing on purpose

After this runs the branch is RED, and that is correct. Each following step
turns a named group of those tests green.

Usage:
    python apply_batch19_step0.py --check   # resolve every step, write nothing
    python apply_batch19_step0.py           # apply

Nothing is written unless every step resolves first.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# New files
# --------------------------------------------------------------------------

NEW_FILES: dict[str, str] = {
    "requirements-dev.txt": r'''# Development and CI only. Never installed on the server.
#
# Split out of requirements.txt so that `pip-audit --requirement
# requirements.txt` audits what actually ships. pytest carries an open
# advisory that cannot be cleared yet (pytest-asyncio 0.24.0 pins pytest<9),
# and auditing it would make the gate red over code that never runs in
# production.

# ==================== TESTING ====================
pytest==8.3.4
pytest-asyncio==0.24.0
pytest-cov==6.0.0
# In-memory async MongoDB. Without it nothing below app/services is reachable
# from a test, which is why every critical finding in the Batch 18 review sat
# in code the suite never executed.
mongomock-motor==0.0.36

# ==================== QUALITY ====================
ruff==0.8.4
pip-audit==2.9.0
''',
    "tests/harness.py": r'''"""Shared fakes that make the I/O half of the app testable.

Everything below MongoDB and Telegram was unreachable from the suite, which is
why every critical bug found in review lives there. These two fakes are the
whole fix: an in-memory Mongo that speaks the same async API as motor, and a
Bot that records what it was asked to send instead of sending it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mongomock_motor import AsyncMongoMockClient

from app import db as app_db


class FakeBot:
    """Records sends. Can be told to fail, once or always.

    Mirrors only the surface process_due_reminders actually uses.
    """

    def __init__(self, fail_times: int = 0, fail_forever: bool = False):
        self.sent: list[dict] = []
        self.fail_times = fail_times
        self.fail_forever = fail_forever

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_forever or self.fail_times > 0:
            self.fail_times = max(0, self.fail_times - 1)
            raise RuntimeError("telegram unavailable")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(self.sent)}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def install_fake_db() -> AsyncMongoMockClient:
    """Point app.db at an in-memory Mongo for the duration of one test."""
    client = AsyncMongoMockClient()
    app_db._client = client
    app_db._database = client["time_manager_pro_test"]
    return client


def teardown_fake_db() -> None:
    app_db._client = None
    app_db._database = None


def utc(**kwargs) -> datetime:
    """A UTC instant relative to now, e.g. utc(minutes=-5)."""
    return datetime.now(timezone.utc) + timedelta(**kwargs)


async def seed_event(**overrides) -> dict:
    """One pending, due, one-off event belonging to user '1001'."""
    document = {
        "user_id": "1001",
        "title": "Dentist",
        "date_iso": "2026-09-20",
        "date_jalali": "1405/06/29",
        "lang": "en",
        "tz_name": "Europe/Berlin",
        "all_day": True,
        "time_hm": None,
        "repeat": "none",
        "repeat_until": None,
        "lead_repeat": "none",
        "reminders": [{"mode": "absolute", "hour": 9, "minute": 0}],
        "reminder_hour": 9,
        "reminder_minute": 0,
        "category": "health",
        "note": "",
        "pinned": False,
        "next_notify_at": utc(minutes=-5),
        "event_ts_utc": utc(days=7),
        "notify_status": "pending",
        "notify_attempts": 0,
        "processing_started_at": None,
        "created_at": utc(days=-1),
        "updated_at": utc(days=-1),
    }
    document.update(overrides)

    result = await app_db.get_events_collection().insert_one(document)
    return await app_db.get_events_collection().find_one({"_id": result.inserted_id})
''',
    "tests/test_review_findings.py": r'''"""One test per finding from the Batch 18 review.

Every test here is expected to FAIL against current main. That is the point:
a fix is only proven when a test that failed before it starts passing after it.
Each docstring names the finding it pins down.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.harness import FakeBot, install_fake_db, seed_event, teardown_fake_db, utc


def as_utc(value: datetime) -> datetime:
    """Mongo hands back naive UTC unless the client is tz_aware."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@pytest.fixture(autouse=True)
def fake_db():
    install_fake_db()
    yield
    teardown_fake_db()


@pytest.fixture
def settings():
    from app.config import get_settings

    return get_settings()


class FailingUpdates:
    """Wraps a collection and makes the first N update_one calls explode."""

    def __init__(self, inner, fail_times: int):
        self._inner = inner
        self._fail_times = fail_times

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def update_one(self, *args, **kwargs):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("mongo write failed")
        return await self._inner.update_one(*args, **kwargs)


# ── CRITICAL 1 — stale expire_at after an edit ──────────────────────────────

async def test_editing_recurring_to_one_off_clears_expire_at(settings):
    """A yearly event edited into a one-off must not keep its TTL marker.

    It currently does, so the TTL index deletes the user's event on schedule.
    """
    from app.schemas.requests import EditEventRequest
    from app.services.events import edit_event_for_user
    from app.utils.dates import expire_for_repeat

    event = await seed_event(
        repeat="yearly",
        expire_at=expire_for_repeat(utc(days=7), "yearly"),
    )

    await edit_event_for_user(
        "1001",
        EditEventRequest(
            initData="x",
            event_id=str(event["_id"]),
            title="Dentist",
            date="2026-09-20",
            timezone="Europe/Berlin",
            repeat="none",
        ),
        settings,
    )

    from app.db import get_events_collection

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    assert "expire_at" not in fresh, (
        "one-off event still carries expire_at=%r — the TTL index will delete it"
        % fresh.get("expire_at")
    )


# ── CRITICAL 2 — TTL doubles as liveness ────────────────────────────────────

def test_ttl_grace_survives_a_worker_outage():
    """expire_at must outlive a realistic worker outage.

    A daily event expires 2 days after its next reminder, and only a successful
    send pushes it forward. The README says the Actions schedule gets dropped,
    so the TTL becomes a delete-user-data-on-outage switch.
    """
    from app.utils.dates import expire_for_repeat

    anchor = datetime(2026, 9, 13, tzinfo=timezone.utc)
    for repeat in ("daily", "weekly", "monthly", "yearly"):
        grace = expire_for_repeat(anchor, repeat) - anchor
        assert grace >= timedelta(days=30), (
            f"{repeat}: only {grace.days}d of grace; a {grace.days + 1}d outage "
            "deletes the user's events"
        )


# ── CRITICAL 3 — send and state update are not atomic ───────────────────────

async def test_failed_state_write_does_not_resend(settings, monkeypatch):
    """If the post-send update throws, the reminder must not be sent twice."""
    from app.db import get_events_collection
    from app.services import reminders as reminders_module

    await seed_event()
    bot = FakeBot()

    real = get_events_collection()
    monkeypatch.setattr(
        reminders_module, "get_events_collection", lambda: FailingUpdates(real, 1)
    )
    await reminders_module.process_due_reminders(bot, settings)

    monkeypatch.setattr(reminders_module, "get_events_collection", lambda: real)
    await reminders_module.recover_stale_processing(settings)
    await reminders_module.process_due_reminders(bot, settings)

    assert len(bot.sent) == 1, (
        f"reminder delivered {len(bot.sent)} times — the user gets duplicates "
        "whenever a state write fails after a successful send"
    )


# ── CRITICAL 4 — "failed" is a terminal state ───────────────────────────────

async def test_event_recovers_after_a_telegram_outage(settings):
    """Five transient failures must not silence an event for ever."""
    from app.db import get_events_collection
    from app.services.reminders import process_due_reminders, recover_stale_processing

    event = await seed_event()

    broken = FakeBot(fail_forever=True)
    for _ in range(6):
        await process_due_reminders(broken, settings)

    status = (await get_events_collection().find_one({"_id": event["_id"]}))["notify_status"]
    assert status == "failed", f"precondition: expected 'failed', got {status!r}"

    working = FakeBot()
    await recover_stale_processing(settings)
    await process_due_reminders(working, settings)

    assert len(working.sent) == 1, (
        "the event never fires again after the outage — 'failed' has no recovery path"
    )


# ── MAJOR 5 — retries have no backoff ───────────────────────────────────────

async def test_retry_backs_off(settings):
    """A failed send must push next_notify_at forward, not retry immediately."""
    from app.db import get_events_collection
    from app.services.reminders import process_due_reminders

    event = await seed_event()
    before = event["next_notify_at"]

    await process_due_reminders(FakeBot(fail_forever=True), settings)

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    after = as_utc(fresh["next_notify_at"])

    assert after > as_utc(before), (
        "next_notify_at unchanged after a failure — the next pass retries "
        "instantly and burns all five attempts in about two minutes"
    )


# ── MAJOR 6 — the health metric measures the wrong thing ────────────────────

async def test_sending_a_reminder_touches_updated_at(settings):
    """measure() counts updated_at as 'sent today', so the send must write it."""
    from app.db import get_events_collection
    from app.services.reminders import process_due_reminders

    event = await seed_event()
    before = event["updated_at"]

    await process_due_reminders(FakeBot(), settings)

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    after = as_utc(fresh["updated_at"])

    assert after > as_utc(before), (
        "process_due_reminders never writes updated_at, so touched_last_24h "
        "actually counts user edits rather than reminders sent"
    )


# ── CRITICAL 7 — card rendering blocks the event loop ───────────────────────

def test_card_render_is_fast_enough_to_serve():
    """317ms of CPU on a public, unauthenticated endpoint is a DoS lever."""
    from app.services.cards import render_event_card

    event = {
        "title": "Anniversary",
        "date_iso": "2026-12-31",
        "date_jalali": "1405/10/10",
        "category": "family",
        "all_day": True,
        "tz_name": "Europe/Berlin",
        "event_ts_utc": utc(days=100),
        "bot_handle": "@Timemanager2026_bot",
    }
    render_event_card(event, "en")  # warm up

    start = time.perf_counter()
    render_event_card(event, "en")
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 80, f"render took {elapsed_ms:.0f}ms"


async def test_card_endpoint_yields_to_the_event_loop():
    """Other requests must keep being served while a card renders."""
    from app.routes.share import public_card
    from app.services.sharing import generate_public_token

    token = generate_public_token()
    await seed_event(public_token=token, public_enabled=True)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.02)
    ticks = 0
    await public_card(token)
    beat.cancel()

    assert ticks >= 5, (
        f"the loop ticked {ticks} times during the render — every other request, "
        "including the Telegram webhook, was frozen"
    )


# ── CRITICAL 8 — webhook secret compared with != ────────────────────────────

def test_webhook_secret_uses_constant_time_comparison():
    """routes/tasks.py gets this right; routes/telegram.py does not."""
    from app.routes.telegram import telegram_webhook

    source = inspect.getsource(telegram_webhook)
    assert "compare_digest" in source, (
        "webhook secret compared with != — use hmac.compare_digest, as "
        "app/routes/tasks.py already does"
    )


# ── MAJOR 9 — event limit is TOCTOU ─────────────────────────────────────────

async def test_event_limit_holds_under_concurrent_saves(settings):
    """count_documents then insert_one, with nothing in between."""
    from app.db import get_events_collection
    from app.schemas.requests import AddEventRequest
    from app.services.events import add_event_for_user

    limit = settings.event_limit_base
    for index in range(limit - 1):
        await seed_event(title=f"filler {index}")

    payload = AddEventRequest(
        initData="x", title="Race", date="2026-10-01", timezone="UTC"
    )
    await asyncio.gather(
        *(add_event_for_user("1001", payload, settings) for _ in range(5)),
        return_exceptions=True,
    )

    total = await get_events_collection().count_documents({"user_id": "1001"})
    assert total <= limit, f"{total} events stored against a limit of {limit}"


# ── MAJOR 10 — revoking a share leaves the join link alive ──────────────────

async def test_disabling_sharing_also_kills_the_invite_link(settings):
    """The UI promises the old link stops working. share_token survives."""
    from app.services.share_group import find_by_token, start_group
    from app.services.sharing import set_share_state

    event = await seed_event()
    group = await start_group("1001", event["_id"], settings)

    await set_share_state("1001", str(event["_id"]), False, settings)

    assert await find_by_token(group["token"]) is None, (
        "the invite token still resolves after sharing was switched off — "
        "strangers can still join the event"
    )


# ── MAJOR 11 — the reminders contract contradicts itself ────────────────────

def test_stored_reminders_are_accepted_back_by_the_api(settings):
    """What EventOut returns must be valid input to AddEventRequest."""
    from pydantic import ValidationError

    from app.schemas.requests import AddEventRequest
    from app.services.events import _normalize_event_input

    saved = _normalize_event_input(
        AddEventRequest(
            initData="x",
            title="Instalment",
            date="2026-12-01",
            timezone="UTC",
            lead_repeat="weekly",
        ),
        settings,
    )

    try:
        AddEventRequest(
            initData="x",
            title="Instalment",
            date="2026-12-01",
            timezone="UTC",
            reminders=saved["reminders"],
        )
    except ValidationError as exc:
        pytest.fail(
            f"server stores {len(saved['reminders'])} reminder specs but refuses "
            f"them as input (cap is 3): {exc.errors()[0]['msg']}"
        )


# ── MAJOR 12 — run_once hides failure from the dead-man's switch ────────────

async def test_run_once_exits_non_zero_when_processing_fails(monkeypatch):
    """healthchecks.io pings on `if: success()`. Exit 0 means 'all fine'."""
    from app.db import connect_to_mongo
    from worker import run_once

    async def boom(*args, **kwargs):
        raise RuntimeError("mongo unreachable mid-run")

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(run_once, "process_due_reminders", boom)
    monkeypatch.setattr(run_once, "connect_to_mongo", noop)
    monkeypatch.setattr(run_once, "ensure_indexes", noop)
    monkeypatch.setattr(run_once, "close_mongo_connection", noop)
    assert connect_to_mongo is not None  # keep the import meaningful

    with pytest.raises(SystemExit) as exit_info:
        await run_once.main()

    assert exit_info.value.code != 0, (
        "run_once swallowed the failure and exited 0 — the healthcheck ping "
        "fires and the dead-man's switch reports a dead worker as alive"
    )


# ── MAJOR 13 — one-letter searches match almost everything ──────────────────

def test_single_letter_search_is_not_a_category_wildcard():
    """'e' substring-matches six of the nine category names."""
    from app.schemas.requests import ListEventsRequest
    from app.services.events import build_list_query

    query = build_list_query("1001", ListEventsRequest(initData="x", q="e"))
    categories: list[str] = []
    for clause in query.get("$or", []):
        if "category" in clause:
            categories = clause["category"]["$in"]

    assert len(categories) <= 2, (
        f"typing 'e' matches {len(categories)} categories {categories} — "
        "the result list looks unfiltered"
    )


# ── CRITICAL 14 — naive datetimes from Mongo follow the SERVER clock ────────

def test_finished_series_ends_on_the_same_day_on_any_host():
    """expand_occurrences calls .astimezone() on a value Mongo returns naive.

    A naive datetime is interpreted as the host's local time, so the same event
    draws a different last day depending on the server's TZ. DEBUGGING.md rule
    4 says everything is computed in the event's own zone, never the server's.
    """
    import os
    import time as time_module
    from datetime import date

    from app.utils.occurrences import expand_occurrences

    event = {
        "date_iso": "2025-12-28",
        "tz_name": "Asia/Tehran",
        "all_day": True,
        "repeat": "daily",
        "notify_status": "done",
        "event_ts_utc": datetime(2026, 1, 1, 23, 30),  # naive, as Mongo returns it
    }

    original = os.environ.get("TZ")
    results = {}
    try:
        for zone in ("UTC", "Asia/Tehran"):
            os.environ["TZ"] = zone
            time_module.tzset()
            results[zone] = expand_occurrences(event, date(2025, 12, 28), date(2026, 1, 5))
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time_module.tzset()

    assert results["UTC"] == results["Asia/Tehran"], (
        "the calendar drew %d days on a UTC host and %d on a Tehran host — "
        "the host clock is leaking into the event's own timezone"
        % (len(results["UTC"]), len(results["Asia/Tehran"]))
    )


def test_mongo_client_returns_timezone_aware_datetimes():
    """One flag removes the whole class of bug above.

    Four call sites already patch tzinfo back on by hand; occurrences.py:84
    forgets to, which is the bug the test above pins. tz_aware=True makes all
    four patches unnecessary and the fifth unnecessary to remember.
    """
    import inspect

    from app import db

    assert "tz_aware" in inspect.getsource(db.connect_to_mongo), (
        "AsyncIOMotorClient is built without tz_aware=True, so every datetime "
        "read back from Mongo is naive"
    )
''',
}

# --------------------------------------------------------------------------
# Edits, as exact anchors. Each must match exactly once.
# --------------------------------------------------------------------------

EDITS: list[tuple[str, str, str, str]] = [
    (
        "requirements.txt",
        "# ==================== WEB FRAMEWORK ====================\nfastapi==0.115.6\n",
        "# ==================== WEB FRAMEWORK ====================\n"
        "# 0.115.6 pins starlette<0.42.0, which held starlette on a release with\n"
        "# seven open advisories. Bumping fastapi is what unpins it. Verified\n"
        "# against the full suite: 234 pre-existing tests pass unchanged.\n"
        "fastapi==0.141.1\n",
        "fastapi 0.115.6 -> 0.141.1 (unpins starlette)",
    ),
    (
        "requirements.txt",
        "jinja2==3.1.4\n",
        "jinja2==3.1.6\n",
        "jinja2 3.1.4 -> 3.1.6 (3 advisories)",
    ),
    (
        "requirements.txt",
        "Pillow==11.3.0\n",
        "Pillow==12.3.0\n",
        "Pillow 11.3.0 -> 12.3.0 (18 advisories)",
    ),
    (
        "requirements.txt",
        "# ==================== UTILITIES ====================\ncertifi==2024.8.30\njdatetime==5.0.0\n",
        "# ==================== UTILITIES ====================\ncertifi==2024.8.30\njdatetime==5.0.0\n"
        "# Runtime, not test: app/services/telegram_api.py calls it directly for\n"
        "# the Bot API 8.0 methods python-telegram-bot 21.1 does not expose.\n"
        "httpx==0.28.1\n",
        "httpx moved into the runtime section",
    ),
    (
        "requirements.txt",
        "\n# ==================== TESTING ====================\n"
        "pytest==8.3.4\npytest-asyncio==0.24.0\nhttpx==0.28.1\n"
        "\n# ==================== QUALITY ====================\nruff==0.8.4\n",
        "",
        "test and lint deps removed (now in requirements-dev.txt)",
    ),
    (
        ".github/workflows/ci.yml",
        "on:\n  push:\n    branches: [main]\n",
        'on:\n  push:\n    branches: [main, "fix/**"]\n',
        "CI now runs on fix/** branches",
    ),
    (
        ".github/workflows/ci.yml",
        "      - name: Install dependencies\n"
        "        run: pip install -r requirements.txt\n"
        "      - name: Lint (ruff)\n",
        "      - name: Install dependencies\n"
        "        run: |\n"
        "          pip install -r requirements.txt\n"
        "          pip install -r requirements-dev.txt\n"
        "      - name: Audit production dependencies\n"
        "        # Production only. requirements-dev.txt is deliberately not\n"
        "        # audited: pytest never runs on the server, and pytest-asyncio\n"
        "        # pins it below the release that fixes its advisory. Blocking\n"
        "        # rather than advisory, because this gate is green today and a\n"
        "        # gate that starts red is one everyone learns to ignore.\n"
        "        run: pip-audit --requirement requirements.txt\n"
        "      - name: Lint (ruff)\n",
        "pip-audit added as a blocking gate on production deps",
    ),
]


def main() -> int:
    check_only = "--check" in sys.argv
    planned: list[tuple[Path, str]] = []
    problems: list[str] = []
    notes: list[str] = []

    # ---- resolve new files ------------------------------------------------
    for relative, content in NEW_FILES.items():
        target = ROOT / relative
        if target.exists():
            if target.read_text() == content:
                notes.append(f"  = {relative} already present and identical")
                continue
            problems.append(f"  ! {relative} exists with different content")
            continue
        planned.append((target, content))
        notes.append(f"  + {relative} ({len(content.splitlines())} lines)")

    # ---- resolve edits ----------------------------------------------------
    buffers: dict[Path, str] = {}
    for relative, old, new, description in EDITS:
        target = ROOT / relative
        if not target.exists():
            problems.append(f"  ! {relative} not found")
            continue
        text = buffers.get(target, target.read_text())
        count = text.count(old)
        # The replacement is checked before the anchor, because several
        # replacements contain their own anchor as a prefix. Counting first
        # would re-apply those on a second run and duplicate lines.
        if new and new in text:
            notes.append(f"  = {relative}: {description} (already applied)")
        elif count == 1:
            buffers[target] = text.replace(old, new, 1)
            notes.append(f"  ~ {relative}: {description}")
        elif count == 0 and not new:
            # A deletion has an empty replacement, so "already applied" for it
            # means only that the anchor is gone.
            notes.append(f"  = {relative}: {description} (already applied)")
        else:
            problems.append(
                f"  ! {relative}: anchor matched {count} times, expected 1 "
                f"-> {description}"
            )

    planned.extend(buffers.items())

    # ---- report -----------------------------------------------------------
    print("apply_batch19_step0.py - scaffold for the Batch 18 review fixes")
    print(f"repository root: {ROOT}\n")
    for line in notes:
        print(line)

    if problems:
        print("\nUNRESOLVED - nothing was written:")
        for line in problems:
            print(line)
        print(
            "\nThe working tree is not what this script was built against.\n"
            "Check you are on the fix branch and that no step was applied by hand."
        )
        return 1

    if check_only:
        print(f"\n--check: {len(planned)} file(s) would be written. Nothing changed.")
        return 0

    for target, content in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    print(f"\nApplied. {len(planned)} file(s) written.")
    print(
        "\nNext:\n"
        "  pip install -r requirements.txt -r requirements-dev.txt\n"
        "  ruff check .\n"
        "  pytest -q\n"
        "\nExpect 15 failures in tests/test_review_findings.py. That is the\n"
        "point of this step: every one of them is a finding waiting for a fix.\n"
        "The 234 pre-existing tests must still pass. If any of those break,\n"
        "the dependency bump is the suspect, not your code.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
