from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.responses import EventOut


@pytest.mark.anyio
async def test_health_endpoint(async_client) -> None:
    from app.routes import health as health_module

    async def fake_ping_database() -> bool:
        return True

    original = health_module.ping_database
    health_module.ping_database = fake_ping_database
    try:
        response = await async_client.get("/health")
    finally:
        health_module.ping_database = original

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert "ts" in data


@pytest.mark.anyio
async def test_api_list_returns_targets(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
        return "123"

    async def fake_list_events_for_user(user_id: str, payload):
        return (
            [
                EventOut(
                    id="evt1",
                    title="Birthday",
                    date_iso="2026-04-20",
                    date_jalali="1405/01/31",
                    repeat="yearly",
                    notify_status="pending",
                    tz_name="UTC",
                    category="birthday",
                    pinned=True,
                    note="Cake",
                )
            ],
            False,
        )

    original_auth = events_module.get_authenticated_user_id
    original_list = events_module.list_events_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.list_events_for_user = fake_list_events_for_user

    try:
        response = await async_client.post(
            "/api/list",
            json={"initData": "dummy", "skip": 0},
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.list_events_for_user = original_list

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["targets"]) == 1
    assert data["targets"][0]["title"] == "Birthday"
    assert data["meta"]["returned"] == 1
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_api_add_success(async_client) -> None:
    from app.routes import events as events_module

    captured: dict = {}

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
        return "123"

    async def fake_add_event_for_user(user_id: str, payload) -> None:
        captured["user_id"] = user_id
        captured["title"] = payload.title
        captured["date"] = payload.date

    original_auth = events_module.get_authenticated_user_id
    original_add = events_module.add_event_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.add_event_for_user = fake_add_event_for_user

    try:
        response = await async_client.post(
            "/api/add",
            json={
                "initData": "dummy",
                "title": "Doctor Visit",
                "date": "2026-05-01",
                "timezone": "UTC",
                "repeat": "none",
                "category": "health",
                "note": "",
                "pinned": False,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.add_event_for_user = original_add

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert captured["user_id"] == "123"
    assert captured["title"] == "Doctor Visit"
    assert captured["date"] == "2026-05-01"


@pytest.mark.anyio
async def test_api_add_rejects_invalid_payload(async_client) -> None:
    response = await async_client.post(
        "/api/add",
        json={
            "initData": "dummy",
            "title": "",
            "date": "2026/05/01",
            "timezone": "UTC",
            "repeat": "none",
            "category": "health",
            "note": "",
            "pinned": False,
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_api_note_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
        return "123"

    async def fake_save_note_for_user(user_id: str, payload) -> str:
        assert user_id == "123"
        assert payload.event_id == "event123"
        return payload.note

    original_auth = events_module.get_authenticated_user_id
    original_save = events_module.save_note_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.save_note_for_user = fake_save_note_for_user

    try:
        response = await async_client.post(
            "/api/note",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "note": "Buy candles",
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.save_note_for_user = original_save

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["note"] == "Buy candles"


@pytest.mark.anyio
async def test_api_pin_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str, **kwargs) -> str:
        return "123"

    async def fake_set_pin_for_user(user_id: str, payload) -> bool:
        assert user_id == "123"
        return payload.pinned

    original_auth = events_module.get_authenticated_user_id
    original_pin = events_module.set_pin_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.set_pin_for_user = fake_set_pin_for_user

    try:
        response = await async_client.post(
            "/api/pin",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "pinned": True,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.set_pin_for_user = original_pin

    assert response.status_code == 200
    assert response.json() == {"success": True, "pinned": True}


@pytest.mark.anyio
async def test_root_redirects_to_webapp(async_client) -> None:
    response = await async_client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/webapp"


def _event_payload(**overrides):
    from app.schemas.requests import AddEventRequest

    base = {
        "initData": "dummy",
        "title": "Standup",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "none",
    }
    base.update(overrides)
    return AddEventRequest(**base)


def test_normalize_event_input_defaults_to_an_all_day_event() -> None:
    """A client that has not been updated sends neither all_day nor reminders;
    it must keep producing exactly the old schedule."""
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(_event_payload(reminder_hour=7, reminder_minute=30))

    assert doc["all_day"] is True
    assert doc["time_hm"] is None
    assert doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]
    assert doc["reminder_hour"] == 7
    assert doc["notify_status"] == "pending"


def test_normalize_event_input_schedules_a_relative_reminder() -> None:
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(
            all_day=False,
            time_hm="14:30",
            reminders=[{"mode": "relative", "offset_minutes": 60}],
        )
    )

    assert doc["all_day"] is False
    assert doc["time_hm"] == "14:30"
    assert doc["event_ts_utc"] - doc["next_notify_at"] == timedelta(minutes=60)


def test_normalize_event_input_requires_a_time_when_not_all_day() -> None:
    from fastapi import HTTPException

    from app.services.events import _normalize_event_input

    with pytest.raises(HTTPException) as excinfo:
        _normalize_event_input(_event_payload(all_day=False))

    assert excinfo.value.detail == "TIME_REQUIRED"


def test_normalize_event_input_marks_a_finished_series_done() -> None:
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(date="2020-01-01", repeat="daily", repeat_until="2020-01-02")
    )

    assert doc["next_notify_at"] is None
    assert doc["notify_status"] == "done"


def test_add_request_accepts_the_exact_payload_the_mini_app_sends() -> None:
    """The request models forbid unknown keys, so a field added in app.js and
    forgotten here would 422 in production while every unit test still passed.
    These two dicts are the payloads submitEventForm builds, key for key."""
    from app.schemas.requests import AddEventRequest
    from app.services.events import _normalize_event_input

    all_day_payload = {
        "initData": "dummy",
        "title": "Mom birthday",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "yearly",
        "category": "birthday",
        "note": "",
        "pinned": False,
        "all_day": True,
        "time_hm": None,
        "reminders": [{"mode": "absolute", "hour": 7, "minute": 30}],
        "reminder_hour": 7,
        "reminder_minute": 30,
        "repeat_until": None,
    }
    timed_payload = {
        **all_day_payload,
        "title": "Team standup",
        "repeat": "daily",
        "category": "work",
        "all_day": False,
        "time_hm": "14:30",
        "reminders": [{"mode": "relative", "offset_minutes": 60}],
    }

    all_day_doc = _normalize_event_input(AddEventRequest(**all_day_payload))
    assert all_day_doc["time_hm"] is None
    assert all_day_doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]

    timed_doc = _normalize_event_input(AddEventRequest(**timed_payload))
    assert timed_doc["time_hm"] == "14:30"
    assert timed_doc["event_ts_utc"] - timed_doc["next_notify_at"] == timedelta(minutes=60)


def test_next_occurrence_is_the_series_start_for_a_one_off_event() -> None:
    from app.services.events import next_occurrence_iso

    doc = {
        "date_iso": "2026-04-20",
        "tz_name": "Europe/Berlin",
        "event_ts_utc": datetime(2026, 4, 19, 22, 0, tzinfo=timezone.utc),
    }
    assert next_occurrence_iso(doc) == "2026-04-20"


def test_next_occurrence_follows_the_advanced_timestamp() -> None:
    """A yearly birthday from 2020 counts down to the next one, not to 2020 —
    the worker moves event_ts_utc forward while date_iso stays put."""
    from app.services.events import next_occurrence_iso, serialize_event

    doc = {
        "_id": "x",
        "title": "Mom birthday",
        "date_iso": "2020-04-20",
        "date_jalali": "1399/02/01",
        "repeat": "yearly",
        "notify_status": "pending",
        "tz_name": "Europe/Berlin",
        "category": "birthday",
        "pinned": False,
        "note": "",
        "event_ts_utc": datetime(2027, 4, 19, 22, 0, tzinfo=timezone.utc),
    }

    assert next_occurrence_iso(doc) == "2027-04-20"

    out = serialize_event(doc)
    assert out.date_iso == "2020-04-20"
    assert out.next_date_iso == "2027-04-20"
    assert out.next_date_jalali != out.date_jalali


def test_next_occurrence_falls_back_when_the_timestamp_is_missing() -> None:
    from app.services.events import next_occurrence_iso

    assert next_occurrence_iso({"date_iso": "2026-04-20"}) == "2026-04-20"


def test_one_off_events_are_not_given_a_ttl() -> None:
    """expire_at is what the TTL index deletes on. A one-off event keeps no
    expiry, so the archive survives; a series still gets its safety net."""
    from app.services.events import _normalize_event_input

    once = _normalize_event_input(_event_payload(repeat="none"))
    series = _normalize_event_input(_event_payload(repeat="yearly"))

    assert "expire_at" not in once
    assert "expire_at" in series
