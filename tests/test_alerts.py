"""Alerts by Telegram and healthchecks.io (Batch 25, Stage 3b, ADR 0014).

As chosen: an alert for late or stuck reminders, for failures in the last 24
hours, and for any failed reminder at all; sent when a problem starts,
repeated every six hours while it lasts, and a message when it is over. The
old path sent the whole report after every run while anything was wrong: once
a minute with the cron, for as long as a single failed reminder existed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.config import get_settings
from tests.harness import install_fake_db, seed_event, teardown_fake_db

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SECRET = "s" * 32
PING = "https://hc-ping.example/check-uuid"


def alerts():
    import app.services.alerts as module

    return module


def stats(**numbers) -> dict:
    base = {"overdue": 0, "worst_late_minutes": 0, "stuck": 0, "failed": 0, "failed_last_24h": 0,
            "due_next_24h": 0, "touched_last_24h": 0, "checked_at": NOW}
    base.update(numbers)
    return base


class Telegram:
    sent: list[tuple] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, chat_id, text, **kwargs):
        Telegram.sent.append((chat_id, text))


@pytest.fixture
def env(monkeypatch):
    import app.routes.tasks as tasks

    module = alerts()
    Telegram.sent = []
    pings: list[str] = []

    async def record(url: str) -> None:
        pings.append(url)

    monkeypatch.setattr(module, "Bot", Telegram)
    monkeypatch.setattr(tasks, "Bot", Telegram)
    monkeypatch.setattr(module, "_get", record)
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_chat_id", 4242)
    monkeypatch.setattr(settings, "tasks_secret", SECRET)
    monkeypatch.setattr(settings, "healthcheck_ping_url", PING, raising=False)
    install_fake_db()
    yield type("Env", (), {"sent": Telegram.sent, "pings": pings, "module": module})
    teardown_fake_db()


async def check(numbers: dict, at: datetime):
    return await alerts().check_and_alert(numbers, now=at)


async def test_a_problem_alerts_once_when_it_starts(env):
    assert await check(stats(overdue=2, worst_late_minutes=14), NOW) == "started"
    assert await check(stats(overdue=2, worst_late_minutes=15), NOW + timedelta(minutes=1)) is None
    assert len(env.sent) == 1 and env.sent[0][0] == 4242 and "14 min" in env.sent[0][1]


async def test_it_repeats_every_six_hours_while_it_lasts(env):
    await check(stats(stuck=1), NOW)
    assert await check(stats(stuck=1), NOW + timedelta(hours=5, minutes=59)) is None
    assert await check(stats(stuck=1), NOW + timedelta(hours=6)) == "repeated"
    assert len(env.sent) == 2


async def test_it_says_when_it_is_over(env):
    await check(stats(overdue=1, worst_late_minutes=11), NOW)
    assert await check(stats(), NOW + timedelta(minutes=30)) == "recovered"
    assert await check(stats(), NOW + timedelta(minutes=31)) is None
    assert len(env.sent) == 2 and "30 min" in env.sent[-1][1]


@pytest.mark.parametrize("reason", ["overdue", "stuck", "failed_last_24h", "failed"])
async def test_every_chosen_reason_raises_an_alert(env, reason):
    assert await check(stats(**{reason: 1}), NOW) == "started"


async def test_two_triggers_at_the_same_moment_send_it_once(env):
    results = await asyncio.gather(check(stats(overdue=1), NOW), check(stats(overdue=1), NOW))
    assert sorted(map(str, results)) == ["None", "started"] and len(env.sent) == 1


async def test_no_admin_chat_means_no_alert(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_chat_id", None)
    assert await check(stats(overdue=1), NOW) is None and not env.sent


async def test_measure_counts_the_failures_of_the_last_24_hours(env):
    from app.services.health import measure

    now = datetime.now(timezone.utc)
    await seed_event(notify_status="failed", updated_at=now - timedelta(hours=2))
    await seed_event(notify_status="failed", updated_at=now - timedelta(days=3))
    numbers = await measure(get_settings())
    assert (numbers["failed"], numbers["failed_last_24h"]) == (2, 1)


async def post(path: str, secret: str | None = SECRET):
    headers = {"X-Tasks-Secret": secret} if secret else {}
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.post(path, headers=headers)


async def test_the_cron_endpoint_alerts_and_pings_healthchecks(env, monkeypatch):
    import app.routes.tasks as tasks

    async def late(settings):
        return stats(overdue=1, worst_late_minutes=12)

    monkeypatch.setattr(tasks, "measure", late)
    response = await post("/tasks/run-reminders")
    assert response.status_code == 200, response.text
    assert env.pings == [PING] and len(env.sent) == 1


async def test_a_failed_run_pings_fail_and_still_fails(env, monkeypatch):
    import app.routes.tasks as tasks

    async def broken(bot, settings):
        raise RuntimeError("database down")

    monkeypatch.setattr(tasks, "process_due_reminders", broken)
    with pytest.raises(RuntimeError):
        await post("/tasks/run-reminders")
    assert env.pings == [PING + "/fail"]


async def test_the_alert_path_can_be_test_fired(env):
    assert (await post("/tasks/alert-test", secret=None)).status_code in (401, 403, 503)
    response = await post("/tasks/alert-test")
    assert response.status_code == 200 and response.json() == {"telegram": True, "healthchecks": True}
    assert len(env.sent) == 1 and "Test alert" in env.sent[0][1] and env.pings == [PING]


def test_every_alert_reason_has_a_section_in_the_runbook():
    from pathlib import Path

    runbook = (Path(__file__).resolve().parents[1] / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    missing = [name for name in ("late", "stuck", "failed_24h", "failed") if f"\n## {name}\n" not in runbook]
    assert not missing and "## The cron check is down" in runbook and "## Test-firing" in runbook, missing
    assert "docs/RUNBOOK.md" in alerts().alert_text("started", stats(overdue=1))
