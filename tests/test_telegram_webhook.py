from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_webhook_rejects_missing_secret(async_client) -> None:
    response = await async_client.post("/telegram/webhook", json={"update_id": 1})
    assert response.status_code == 403


@pytest.mark.anyio
async def test_webhook_rejects_wrong_secret(async_client) -> None:
    response = await async_client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_webhook_accepts_correct_secret_no_callback(async_client) -> None:
    from app.config import get_settings

    secret = get_settings().telegram_webhook_secret
    response = await async_client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.anyio
async def test_handle_snooze_callback_updates_event_for_owner() -> None:
    from app.services import reminders as reminders_module

    calls: dict = {}

    class FakeCollection:
        async def find_one_and_update(self, query, update):
            calls["query"] = query
            calls["update"] = update
            return {"_id": query["_id"]}

    original = reminders_module.get_events_collection
    reminders_module.get_events_collection = lambda: FakeCollection()
    try:
        ok = await reminders_module.handle_snooze_callback(
            "507f1f77bcf86cd799439011", 12345, seconds=3600
        )
    finally:
        reminders_module.get_events_collection = original

    assert ok is True
    # Same {_id, user_id} authorization pattern as the rest of the app —
    # a snooze request can only touch an event owned by the requesting user.
    assert calls["query"]["user_id"] == 12345
    assert "next_notify_at" in calls["update"]["$set"]
    assert calls["update"]["$set"]["notify_status"] == "pending"


@pytest.mark.anyio
async def test_handle_snooze_callback_rejects_invalid_id() -> None:
    from app.services.reminders import handle_snooze_callback

    ok = await handle_snooze_callback("not-a-valid-object-id", 12345, seconds=3600)
    assert ok is False


@pytest.mark.anyio
async def test_handle_snooze_callback_returns_false_when_not_found_or_not_owner() -> None:
    from app.services import reminders as reminders_module

    class FakeCollection:
        async def find_one_and_update(self, query, update):
            return None  # no doc matched _id + user_id together

    original = reminders_module.get_events_collection
    reminders_module.get_events_collection = lambda: FakeCollection()
    try:
        ok = await reminders_module.handle_snooze_callback(
            "507f1f77bcf86cd799439011", 99999, seconds=3600
        )
    finally:
        reminders_module.get_events_collection = original

    assert ok is False
