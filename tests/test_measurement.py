"""Measuring before optimising (Batch 30, the performance review).

Production's RED lines showed POST /api/add at about 2.2 s, but since decision
S1 its Telegram confirmation runs as a background task after the response,
and the middleware measured until the whole request had finished. duration_ms
is now the time until the response is sent, which is what a user waits for,
and total_ms adds the work after it. Each request line carries its database
cost, and a reminder run its phases, so the slow part is found, not guessed.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import app.main as main_module
from app.config import get_settings
from tests.harness import install_fake_db, teardown_fake_db


def red_lines(caplog):
    return [r for r in caplog.records if getattr(r, "event", None) == "request"]


async def test_background_work_is_not_counted_as_the_wait(caplog):
    from app.observability import RequestContextMiddleware

    async def later():
        await asyncio.sleep(0.3)

    async def page(request):
        return PlainTextResponse("ok", background=BackgroundTask(later))

    app = RequestContextMiddleware(Starlette(routes=[Route("/slow-after", page)]))
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client:
        await client.get("/slow-after")
    line = red_lines(caplog)[-1]
    seen = (line.duration_ms, getattr(line, "total_ms", None))
    assert line.duration_ms < 250 and line.total_ms >= 300, seen


async def test_every_request_line_carries_its_database_cost(caplog):
    caplog.set_level(logging.INFO)
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        await client.get("/health")
    line = red_lines(caplog)[-1]
    assert isinstance(line.db_calls, int) and isinstance(line.db_ms, float)


def test_database_commands_are_counted_against_their_request():
    from app.observability import DATABASE_COST, RequestCost, request_cost_var

    DATABASE_COST.succeeded(SimpleNamespace(duration_micros=999))  # outside a request: ignored
    cost = RequestCost()
    token = request_cost_var.set(cost)
    try:
        DATABASE_COST.succeeded(SimpleNamespace(duration_micros=1500))
        DATABASE_COST.failed(SimpleNamespace(duration_micros=500))
    finally:
        request_cost_var.reset(token)
    assert (cost.calls, round(cost.ms, 1)) == (2, 2.0)


async def test_the_database_client_reports_its_commands(monkeypatch):
    import app.db as db
    from app.observability import DATABASE_COST

    class Client:
        made: dict = {}

        def __init__(self, *args, **kwargs):
            Client.made = kwargs

        def __getitem__(self, name):
            return self

        async def command(self, *args, **kwargs):
            return {"ok": 1}

    monkeypatch.setattr(db, "AsyncIOMotorClient", Client)
    monkeypatch.setattr(db, "_client", None)
    monkeypatch.setattr(db, "_database", None)
    from app.db import connect_to_mongo

    await connect_to_mongo()
    assert DATABASE_COST in Client.made.get("event_listeners", []), Client.made


async def test_a_reminder_run_logs_its_phases(caplog, monkeypatch):
    import app.routes.tasks as tasks

    class Quiet:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "Bot", Quiet)
    monkeypatch.setattr(get_settings(), "tasks_secret", "s" * 32)
    caplog.set_level(logging.INFO)
    install_fake_db()
    try:
        transport = ASGITransport(app=main_module.app)
        async with AsyncClient(transport=transport, base_url="https://testserver") as client:
            response = await client.post("/tasks/run-reminders", headers={"X-Tasks-Secret": "s" * 32})
    finally:
        teardown_fake_db()
    assert response.status_code == 200, response.text
    run = next(r for r in caplog.records if getattr(r, "event", None) == "reminder_run")
    phases = {"recover_ms", "bot_start_ms", "process_ms", "bot_stop_ms", "measure_ms", "alert_ms", "ping_ms"}
    assert phases <= set(vars(run)), sorted(phases - set(vars(run)))
