"""One test per finding from the Batch 18 review.

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

async def test_event_recovers_after_a_transient_outage(settings):
    """A transient outage must not silence an event for ever.

    The old code gave up after five failures into a terminal "failed" state
    nothing ever revived, and because it retried instantly those five were
    spent within about two minutes of the first one.
    """
    from app.db import get_events_collection
    from app.services.reminders import process_due_reminders

    await seed_event()

    async def backoff_window_elapses():
        """Stands in for real time passing between cron ticks."""
        await get_events_collection().update_many(
            {"notify_status": "pending"},
            {"$set": {"next_notify_at": utc(minutes=-5)}},
        )

    broken = FakeBot(fail_forever=True)
    for _ in range(8):
        await backoff_window_elapses()
        await process_due_reminders(broken, settings)
    assert broken.sent == [], "nothing should have been delivered while it was down"

    working = FakeBot()
    await backoff_window_elapses()
    await process_due_reminders(working, settings)

    assert len(working.sent) == 1, (
        "the event never fired again once the outage ended"
    )


async def test_blocked_user_stops_being_retried(settings):
    """Forbidden is permanent. Backing off and retrying it is pure noise."""
    from telegram.error import Forbidden

    from app.db import get_events_collection
    from app.services.reminders import process_due_reminders

    event = await seed_event()

    class BlockedBot(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            raise Forbidden("bot was blocked by the user")

    await process_due_reminders(BlockedBot(), settings)

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    assert fresh["notify_status"] == "failed", (
        f"expected a blocked user to end in 'failed', got {fresh['notify_status']!r}"
    )


async def test_health_report_sees_a_permanently_failed_reminder(settings):
    """A blocked bot used to look exactly like a healthy queue."""
    from app.services.health import measure

    await seed_event(notify_status="failed", next_notify_at=None)

    stats = await measure(settings)

    assert stats["failed"] == 1, "measure() does not count failed reminders"
    assert stats["healthy"] is False, "a failed reminder must not read as healthy"


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

    assert ticks >= 3, (
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
    # Without this, `async with Bot(token=...)` reaches api.telegram.org and
    # the test both needs the network and fails for the wrong reason.
    monkeypatch.setattr(run_once, "Bot", lambda **kwargs: FakeBot())
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

class _NaiveTrap(datetime):
    """A datetime that refuses to be silently reinterpreted.

    time.tzset() does not exist on Windows, so the host clock cannot be moved
    inside a test there. Trapping the call is portable and pins the same bug:
    .astimezone() on a naive value quietly means "the server's local time".
    """

    def astimezone(self, tz=None):
        if self.tzinfo is None:
            raise AssertionError(
                "astimezone() called on a naive datetime: the value is being "
                "read as the server's local time, not as UTC. DEBUGGING.md "
                "rule 4 says everything is computed in the event's own zone."
            )
        return super().astimezone(tz)


def test_finished_series_is_not_read_in_the_server_timezone():
    """expand_occurrences must not .astimezone() a value Mongo returns naive."""
    from datetime import date

    from app.utils.occurrences import expand_occurrences

    event = {
        "date_iso": "2025-12-28",
        "tz_name": "Asia/Tehran",
        "all_day": True,
        "repeat": "daily",
        "notify_status": "done",
        "event_ts_utc": _NaiveTrap(2026, 1, 1, 23, 30),  # naive, as Mongo returns it
    }

    expand_occurrences(event, date(2025, 12, 28), date(2026, 1, 5))


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


# ── CRITICAL 16 — the "safe" timezone fallback could itself raise ───────────

def test_safe_zoneinfo_never_raises_without_the_iana_database(monkeypatch):
    """The fallback must not need the database that just failed.

    ZoneInfo("UTC") sat outside the try, so on a host with no IANA data the
    safe path threw ZoneInfoNotFoundError and took the request down with it.
    """
    from zoneinfo import ZoneInfoNotFoundError

    from app.utils import dates

    def no_database(key, *args, **kwargs):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(dates, "ZoneInfo", no_database)

    tz, name = dates.safe_zoneinfo("Asia/Tehran")
    assert name == "UTC"
    assert tz.utcoffset(datetime(2026, 1, 1)) == timedelta(0)


# ── Step 3 guards ───────────────────────────────────────────────────────────

def test_category_search_still_matches_real_terms():
    """The wildcard fix must not simply break category search.

    Prefix from one character, substring once the term is long enough to mean
    something. Locks the rule in so it cannot drift to "match nothing".
    """
    from app.schemas.requests import ListEventsRequest
    from app.services.events import build_list_query

    def categories_for(term: str) -> list[str]:
        query = build_list_query("1001", ListEventsRequest(initData="x", q=term))
        for clause in query.get("$or", []):
            if "category" in clause:
                return clause["category"]["$in"]
        return []

    assert categories_for("h") == ["health"]
    assert categories_for("hea") == ["health"]
    assert categories_for("ravel") == ["travel"]
    assert categories_for("work") == ["work"]
    assert categories_for("e") == []


def test_cached_backdrop_is_not_shared_between_cards():
    """The backdrop is cached; the canvas drawn on must still be a copy.

    Without the copy every card after the first would carry the previous
    card's text, and a share link would leak another user's event title.
    """
    from app.services.cards import render_event_card

    base = {
        "date_iso": "2026-12-31",
        "date_jalali": "1405/10/10",
        "category": "family",
        "all_day": True,
        "tz_name": "Europe/Berlin",
        "event_ts_utc": utc(days=100),
        "bot_handle": "@Timemanager2026_bot",
    }

    first = render_event_card({**base, "title": "Alice birthday"}, "en")
    second = render_event_card({**base, "title": "Bob graduation"}, "en")
    third = render_event_card({**base, "title": "Alice birthday"}, "en")

    assert first != second, "two different events rendered byte-identical cards"
    assert first == third, "the same event rendered differently on a second call"
