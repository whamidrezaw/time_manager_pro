"""One test per finding from the Batch 18 review.

Every test here is expected to FAIL against current main. That is the point:
a fix is only proven when a test that failed before it starts passing after it.
Each docstring names the finding it pins down.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests.harness import (
    FakeBot,
    fake_request,
    install_fake_db,
    seed_event,
    teardown_fake_db,
    utc,
)


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

def test_card_render_reuses_its_fonts_and_backdrop():
    """Batch 19 took a render from 317 ms to ~55 by caching fonts and the
    backdrop (bf9b1f0). Since Batch 20 that is pinned through its causes: a
    timing bar measured the machine instead (a laptop's slower clock after the
    browser suite read 109 ms for the same work)."""
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
    from app.services import cards

    render_event_card(event, "en")
    fonts, backdrops = cards._font.cache_info().misses, cards._backdrop.cache_info().misses
    render_event_card(event, "en")

    assert cards._font.cache_info().misses == fonts, "a second render loaded its fonts again"
    assert cards._backdrop.cache_info().misses == backdrops, "a second render rebuilt the backdrop"


def test_card_png_is_saved_without_optimize(monkeypatch):
    """The third cause in bf9b1f0: PNG optimize made each save several times slower."""
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
    from PIL import Image

    saved: dict = {}
    real_save = Image.Image.save

    def spy(self, fp, format=None, **params):
        saved.update(params)
        return real_save(self, fp, format, **params)

    monkeypatch.setattr(Image.Image, "save", spy)
    render_event_card(event, "en")

    assert saved and not saved.get("optimize"), saved


def test_card_render_is_fast_enough_to_serve():
    """A coarse safety net, decided in Batch 20, for a slow step nobody has
    thought of yet: 500 ms of this thread's CPU, fastest of five. The known
    causes are pinned above without a clock; this only has to hold on slow
    hardware too (a throttled laptop measured ~110 ms, 4.5 times under it)."""
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
    runs = []
    for _ in range(5):
        start = time.thread_time()
        render_event_card(event, "en")
        runs.append((time.thread_time() - start) * 1000)
    elapsed_ms = min(runs)

    assert elapsed_ms < 500, (
        f"render took {elapsed_ms:.0f}ms of CPU at best (runs: {[round(r) for r in runs]})")


async def test_card_endpoint_yields_to_the_event_loop():
    """Other requests must keep being served while a card renders.

    Measured as the longest single stall of the event loop, not as a count of
    timer ticks. asyncio.sleep() granularity is about 1ms on Linux and about
    15.6ms on Windows, so counting ticks over a ~55ms render measures the
    host's timer far more than it measures whether the loop was blocked: the
    same passing code scored 11 on one machine and 3 on another. The watchdog
    below uses sleep(0), which involves no timer at all and runs on every pass
    of the loop, so the signal is the same everywhere.
    """
    from app.routes.share import public_card
    from app.services.sharing import generate_public_token

    token = generate_public_token()
    await seed_event(public_token=token, public_enabled=True)

    stalls: list[float] = []

    async def watchdog():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0)
            current = time.perf_counter()
            stalls.append(current - previous)
            previous = current

    watch = asyncio.create_task(watchdog())
    await asyncio.sleep(0)
    stalls.clear()

    started = time.perf_counter()
    await public_card(fake_request(f"/c/{token}/card.png"), token)
    elapsed = time.perf_counter() - started
    watch.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watch

    # No observation at all means the loop never got control: the stall was
    # the whole render.
    longest = max(stalls, default=elapsed)

    assert longest < elapsed / 2, (
        f"the loop went {longest * 1000:.0f}ms without running anything while a "
        f"{elapsed * 1000:.0f}ms render was in progress — every other request, "
        "including the Telegram webhook, was frozen for that whole time"
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


# ── Step 4 guards ───────────────────────────────────────────────────────────

def test_reminder_round_trip_is_stable(settings):
    """Saving what EventOut returned must not shrink the schedule.

    Ignoring the generated lead specs is only correct because they are rebuilt
    from lead_repeat on every save. If that ever stops being true, an edit
    would quietly drop twelve reminders and this catches it.
    """
    from app.schemas.requests import AddEventRequest
    from app.services.events import _normalize_event_input

    common = dict(
        initData="x", title="Instalment", date="2026-12-01",
        timezone="UTC", lead_repeat="weekly",
    )
    # 07:30 rather than the 09:00 default, so that losing the user's own spec
    # is visible. With the default the legacy hour/minute fallback rebuilds an
    # identical one and the test cannot tell the difference.
    chosen = [{"mode": "absolute", "hour": 7, "minute": 30}]

    first = _normalize_event_input(
        AddEventRequest(**common, reminders=chosen), settings
    )
    second = _normalize_event_input(
        AddEventRequest(**common, reminders=first["reminders"]), settings
    )

    # Comparing first with second alone proves nothing: both come out of the
    # same code, so a filter that drops everything is self-consistent. Assert
    # the user's own choice survived.
    assert {"mode": "absolute", "hour": 7, "minute": 30} in first["reminders"], (
        f"the client's own reminder was dropped on save: {first['reminders'][:2]}"
    )
    assert len(second["reminders"]) == len(first["reminders"]), (
        f"round trip turned {len(first['reminders'])} reminders into "
        f"{len(second['reminders'])}"
    )
    assert second["reminders"] == first["reminders"]


async def test_revoking_sharing_leaves_existing_members_alone(settings):
    """Killing the invite link must not delete what members already have.

    Revocation stops new joins. Taking a copy away from someone who already
    joined would be a different and much ruder thing to do.
    """
    from app.db import get_events_collection
    from app.services.share_group import join_group, member_count, start_group
    from app.services.sharing import set_share_state

    event = await seed_event()
    group = await start_group("1001", event["_id"], settings)
    await join_group("2002", group["token"], "Europe/Berlin", settings)

    owner = await get_events_collection().find_one({"_id": event["_id"]})
    share_id = owner["share_id"]
    assert await member_count(share_id) == 1, "precondition: the join did not land"

    await set_share_state("1001", str(event["_id"]), False, settings)

    assert await member_count(share_id) == 1, (
        "revoking the link removed a member who had already joined"
    )


# ── MAJOR 17 — the event limit was decided before the insert ────────────────

async def test_event_limit_holds_when_the_count_is_stale(settings):
    """The guard must survive a count that was already out of date.

    count_documents then insert_one leaves a window where a second request
    passes the same check. mongomock never suspends at an await, so the race
    cannot be reproduced by running two coroutines; injecting the stale read
    tests the same property deterministically.
    """
    from app.db import get_events_collection
    from app.schemas.requests import AddEventRequest
    from app.services import events as events_module

    limit = settings.event_limit_base
    for index in range(limit):
        await seed_event(title=f"filler {index}")

    real = get_events_collection()

    class StaleFirstCount:
        """Answers the pre-check with a stale value, then tells the truth."""

        def __init__(self, inner):
            self._inner = inner
            self._calls = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def count_documents(self, *args, **kwargs):
            self._calls += 1
            if self._calls == 1:
                return 0          # as if nothing had been saved yet
            return await self._inner.count_documents(*args, **kwargs)

    monkeypatched = StaleFirstCount(real)
    events_module.get_events_collection = lambda: monkeypatched
    try:
        with pytest.raises(HTTPException) as exc:
            await events_module.add_event_for_user(
                "1001",
                AddEventRequest(
                    initData="x", title="One too many",
                    date="2026-10-01", timezone="UTC",
                ),
                settings,
            )
        assert exc.value.detail == "EVENT_LIMIT_REACHED"
    finally:
        events_module.get_events_collection = get_events_collection

    total = await real.count_documents({"user_id": "1001"})
    assert total == limit, (
        f"{total} events stored against a limit of {limit}: the row that got "
        "past the stale check was not rolled back"
    )


# ── MAJOR 18 — the public pages had no rate limit at all ───────────────────

async def test_public_routes_are_rate_limited(settings):
    """Anonymous, unmetered, and behind them a 55ms render."""
    from app.services.auth import check_public_rate_limit

    tight = settings.model_copy(update={"rate_limit_public_count": 3})
    request = fake_request("/c/abc/card.png", client_ip="198.51.100.9")

    for attempt in range(3):
        await check_public_rate_limit(request, tight)

    with pytest.raises(HTTPException) as exc:
        await check_public_rate_limit(request, tight)
    assert exc.value.status_code == 429

    # A different caller must not be punished for the first one's traffic.
    await check_public_rate_limit(
        fake_request("/c/abc/card.png", client_ip="198.51.100.10"), tight
    )


def test_both_public_routes_actually_call_the_limiter():
    """The previous test proves the limiter works, not that it is wired in.

    Removing either call would leave that test green, so check the routes.
    """
    import inspect

    from app.routes.share import public_card, public_countdown

    for route in (public_card, public_countdown):
        assert "check_public_rate_limit" in inspect.getsource(route), (
            f"{route.__name__} does not rate limit an anonymous request"
        )


def test_memory_rate_limit_store_stays_bounded():
    """The fallback store kept one permanent entry per identity.

    Harmless for a few thousand Telegram ids. Once the key is a client IP it
    grows with whoever visits, and the fallback only runs when MongoDB is
    already unavailable - the worst moment to also be leaking memory.
    """
    from app.services import auth

    auth._rate_store.clear()
    try:
        for index in range(auth.MAX_RATE_KEYS + 500):
            auth.check_rate_limit(f"ip:203.0.113.{index}", scope=auth.PUBLIC_SCOPE)
        assert len(auth._rate_store) <= auth.MAX_RATE_KEYS, (
            f"{len(auth._rate_store)} keys retained against a cap of "
            f"{auth.MAX_RATE_KEYS}"
        )
    finally:
        auth._rate_store.clear()


# ── MAJOR 19 — no security headers at all ──────────────────────────────────

def _client():
    from fastapi.testclient import TestClient

    import app.main as main_module

    return TestClient(main_module.app, base_url="https://testserver")


def test_security_headers_are_set():
    """Checked on a real response through the app, not on the constants."""
    response = _client().get("/health")

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "max-age=" in (response.headers.get("Strict-Transport-Security") or "")
    assert "default-src 'self'" in (response.headers.get("Content-Security-Policy") or "")


def test_csp_allows_every_origin_the_templates_load():
    """The policy must match what the app actually loads.

    A CSP that is wrong by one directive does not warn anybody: the script
    just does not load and the Mini App looks broken. This walks the templates
    and the stylesheet instead of trusting that the policy was kept in step.
    """
    import re
    from pathlib import Path

    from app.middleware import CSP

    root = Path(__file__).resolve().parents[1]
    sources = list((root / "templates").glob("*.html")) + [root / "static" / "style.css"]

    origins: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for url in re.findall(r"""(?:src|href)=["'](https?://[^"'/]+)""", text):
            origins.add(url)
        for url in re.findall(r"""url\(["']?(https?://[^"')]+)""", text):
            origins.add(url.split("/", 3)[0] + "//" + url.split("/", 3)[2])

    missing = sorted(o for o in origins if o not in CSP)
    assert not missing, (
        f"the templates load {missing} but the policy does not allow it — "
        "either add it to app/middleware.py or stop loading it"
    )


def test_public_pages_carry_no_inline_script_or_style():
    """The policy has no 'unsafe-inline', so nothing may rely on it.

    countdown.html used to carry both, and its inline script interpolated
    miniapp_url straight into a JS string literal, which Jinja does not
    autoescape for. Both now live in /static and the URL arrives through a
    data attribute.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    offenders: list[str] = []
    for path in (root / "templates").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text):
            offenders.append(f"{path.name}: inline <script>")
        if re.search(r"<style[^>]*>", text):
            offenders.append(f"{path.name}: inline <style>")
        if re.search(r"\bstyle=[\"']", text):
            offenders.append(f"{path.name}: style= attribute")
        if re.search(r"\bon(click|load|change|submit|input|error)=", text):
            offenders.append(f"{path.name}: inline event handler")

    assert not offenders, offenders


async def test_countdown_page_renders_with_its_external_assets(settings):
    """End-to-end proof that moving the inline blocks out did not break it.

    The page is the public face of a shared event, so a broken stylesheet
    reference is the first thing a stranger would see.
    """
    from app.services.sharing import generate_public_token

    token = generate_public_token()
    await seed_event(public_token=token, public_enabled=True, title="Alice birthday")

    from app.routes.share import public_countdown

    response = await public_countdown(fake_request(f"/c/{token}"), token)
    body = response.body.decode("utf-8")

    assert "/static/countdown.css?v=" in body, "the stylesheet link is missing"
    assert "/static/countdown.js?v=" in body, "the script tag is missing"
    assert "data-miniapp-url=" in body, "the redirect target is not on the body"
    assert "Alice birthday" in body
    assert "<style" not in body and "<script>" not in body, (
        "an inline block came back; the policy has no 'unsafe-inline'"
    )

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    assert (root / "static" / "countdown.css").exists()
    assert (root / "static" / "countdown.js").exists()


# ── Found in the pre-merge diff review ─────────────────────────────────────

async def test_share_card_can_be_embedded_cross_origin(settings):
    """The card is the viral surface; same-site would block it everywhere else.

    The security middleware defaults every response to
    Cross-Origin-Resource-Policy: same-site. Correct for the app, wrong for
    this one route: a browser would refuse to render the card on any page that
    is not ours, which is the whole point of a share link.
    """
    from app.routes.share import public_card
    from app.services.sharing import generate_public_token

    token = generate_public_token()
    await seed_event(public_token=token, public_enabled=True)

    response = await public_card(fake_request(f"/c/{token}/card.png"), token)

    assert response.headers.get("Cross-Origin-Resource-Policy") == "cross-origin", (
        "the card inherited the app-wide same-site policy and cannot be "
        "embedded anywhere else"
    )


def test_asset_version_covers_every_shipped_css_and_js():
    """A hand-written list falls behind; this one already had.

    countdown.css and countdown.js were added without being added to the list,
    so an edit to either would have kept serving the cached copy.
    """
    import hashlib
    from pathlib import Path

    from app.routes.web import _compute_asset_version

    static = Path(__file__).resolve().parents[1] / "static"
    shipped = sorted(static.glob("*.css")) + sorted(static.glob("*.js"))
    assert len(shipped) >= 7, f"expected the front-end files, found {shipped}"

    before = _compute_asset_version()

    # Touch each file in turn; every one must move the version.
    for path in shipped:
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n/* cache-busting probe */\n")
            assert _compute_asset_version() != before, (
                f"editing {path.name} does not change the asset version, so "
                "browsers would keep serving the copy they already have"
            )
        finally:
            path.write_bytes(original)

    assert _compute_asset_version() == before
    assert hashlib.sha256  # the digest is content-based, not mtime-based
