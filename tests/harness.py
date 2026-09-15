"""Shared fakes that make the I/O half of the app testable.

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
