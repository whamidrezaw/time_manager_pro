"""Speed, from what Batch 30 measured in production (Batch 31, ADR 0019).

Each database command cost about 145 ms, so measure()'s seven commands, sent
one after another, took about a second on every reminder run. Starting a bot
(a new connection, and getMe) took 0.8 to 1 s, and every webhook update, every
reminder run and every background message started one. The counts now go out
at once, and the app starts one bot and every sender uses it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


async def test_the_health_counts_go_out_at_once(monkeypatch):
    import app.services.health as health

    state = {"in_flight": 0, "peak": 0}

    class Events:
        async def _one(self):
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            await asyncio.sleep(0)
            state["in_flight"] -= 1

        async def count_documents(self, *args, **kwargs):
            await self._one()
            return 0

        async def find_one(self, *args, **kwargs):
            await self._one()
            return None

    monkeypatch.setattr(health, "get_events_collection", lambda: Events())
    stats = await health.measure(get_settings())
    assert state["peak"] == 7 and stats["overdue"] == 0, state


class FakeBot:
    made = initialized = closed = 0
    pools: list = []

    def __init__(self, token=None, request=None, **kwargs):
        FakeBot.made += 1
        FakeBot.pools.append(getattr(request, "pool", None))
        self.bot = SimpleNamespace(username="bot", id=1)

    async def initialize(self):
        FakeBot.initialized += 1

    async def shutdown(self):
        FakeBot.closed += 1

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, *exc):
        await self.shutdown()


class Request:
    def __init__(self, connection_pool_size=1, **kwargs):
        self.pool = connection_pool_size


def fresh_counts():
    FakeBot.made = FakeBot.initialized = FakeBot.closed = 0
    FakeBot.pools = []


async def test_without_a_shared_bot_each_sender_starts_its_own(monkeypatch):
    import app.services.telegram_bot as telegram_bot

    fresh_counts()
    monkeypatch.setattr(telegram_bot, "_shared", None)
    async with telegram_bot.telegram_bot(get_settings(), FakeBot) as bot:
        assert isinstance(bot, FakeBot)
    assert (FakeBot.made, FakeBot.initialized, FakeBot.closed) == (1, 1, 1)


async def test_one_shared_bot_is_started_once_and_every_sender_uses_it(monkeypatch):
    import app.services.telegram_bot as telegram_bot

    fresh_counts()
    monkeypatch.setattr(telegram_bot, "Bot", FakeBot)
    monkeypatch.setattr(telegram_bot, "HTTPXRequest", Request)
    monkeypatch.setattr(telegram_bot, "_shared", None)
    shared = await telegram_bot.start_shared_bot(get_settings())
    for _ in range(3):
        async with telegram_bot.telegram_bot(get_settings(), FakeBot) as bot:
            assert bot is shared
    assert (FakeBot.made, FakeBot.initialized, FakeBot.closed) == (1, 1, 0)
    assert FakeBot.pools == [8]  # concurrent senders need more than one connection
    await telegram_bot.stop_shared_bot()
    assert FakeBot.closed == 1 and telegram_bot._shared is None


def test_every_sender_goes_through_the_shared_bot():
    offenders = [f"{path.relative_to(ROOT)}:{number}"
                 for path in (ROOT / "app").rglob("*.py") if path.name != "telegram_bot.py"
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if "async with Bot(" in line]
    assert not offenders, offenders
