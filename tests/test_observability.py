"""Structured logs, request ids and RED lines (Batch 23, Stage 3a).

Decided: JSON lines into Render's own log stream, and the RED numbers (rate,
errors, duration) derived from one line per request and one per reminder run,
not from a metrics server. Every request carries an id from its first log line
to its response, so one user's problem can be followed through the logs.
"""
from __future__ import annotations

import json
import logging
import re

from httpx import ASGITransport, AsyncClient

import app.main as main_module
from tests.harness import install_fake_db, teardown_fake_db

# app.main configures logging from LOG_LEVEL when it is first imported, so it is
# imported here, before any test sets the level it captures at.


def observability():
    import app.observability as module

    return module


async def get(path: str, headers: dict | None = None):
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.get(path, headers=headers or {})


def red_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == "request"]


async def test_every_response_carries_a_request_id():
    observability()
    response = await get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers.get("x-request-id", "")), response.headers


async def test_a_request_id_that_comes_in_is_kept_and_a_bad_one_replaced():
    observability()
    kept = await get("/health", {"X-Request-ID": "tg-12345678"})
    replaced = await get("/health", {"X-Request-ID": "bad id!\\n"})
    assert kept.headers.get("x-request-id") == "tg-12345678"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers.get("x-request-id", ""))


async def test_each_request_writes_one_red_line(caplog):
    observability()
    caplog.set_level(logging.INFO)
    response = await get("/health")

    lines = red_lines(caplog)
    assert len(lines) == 1, [r.getMessage() for r in lines]
    line = lines[0]
    assert (line.route, line.status, line.method) == ("/health", response.status_code, "GET")
    assert line.duration_ms >= 0 and line.request_id == response.headers["x-request-id"]


async def test_a_route_with_a_token_is_logged_by_its_template(caplog):
    observability()
    caplog.set_level(logging.INFO)
    install_fake_db()
    try:
        await get("/c/secret-token-123")
    finally:
        teardown_fake_db()

    lines = red_lines(caplog)
    assert lines and lines[0].route == "/c/{token}"
    # Nothing logs the token: not the RED line, and not httpx's URL lines, which
    # the Telegram API's bot token would appear in the same way.
    leaks = [f"{r.name}: {r.getMessage()}" for r in caplog.records if "secret-token-123" in r.getMessage()]
    assert not leaks, leaks


def test_the_json_formatter_writes_one_object_per_line():
    module = observability()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("tm_pro.test", logging.ERROR, __file__, 1, "sent %s", (3,), sys.exc_info())
    record.event, record.processed = "reminder_run", 3

    entry = json.loads(module.JsonFormatter().format(record))
    assert entry["msg"] == "sent 3" and entry["level"] == "ERROR" and entry["logger"] == "tm_pro.test"
    assert entry["event"] == "reminder_run" and entry["processed"] == 3
    assert "ValueError: boom" in entry["exc"] and "request_id" in entry and entry["ts"].endswith("+00:00")


async def test_a_reminder_run_logs_its_summary(caplog, monkeypatch):
    observability()
    import app.routes.tasks as tasks
    from app.config import get_settings

    class NoTelegram:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "Bot", NoTelegram)
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
    runs = [r for r in caplog.records if getattr(r, "event", None) == "reminder_run"]
    assert len(runs) == 1
    assert {"processed", "recovered", "overdue", "duration_ms"} <= set(vars(runs[0]))


def test_uvicorns_access_log_stays_off_when_a_worker_turns_it_back_on():
    """Found in production (Batch 26): the gunicorn worker sets the access
    logger's handlers and level again after the app is imported, so the level
    set here was undone and every raw path, a public link's token included,
    and every client address went to the logs beside the RED line."""
    from app.config import get_settings

    observability().configure_logging(get_settings())
    access = logging.getLogger("uvicorn.access")
    caught: list[logging.LogRecord] = []

    class Catch(logging.Handler):
        def emit(self, record):
            caught.append(record)

    saved = (access.handlers[:], access.level, access.propagate)
    access.handlers, access.propagate = [Catch()], False  # what uvicorn-worker does
    access.setLevel(logging.INFO)
    try:
        access.info('%s - "%s %s HTTP/%s" %d', "203.0.113.9:0", "GET", "/c/secret-token-123", "1.1", 200)
    finally:
        access.handlers, access.propagate = saved[0], saved[2]
        access.setLevel(saved[1])
    assert not caught, [r.getMessage() for r in caught]
