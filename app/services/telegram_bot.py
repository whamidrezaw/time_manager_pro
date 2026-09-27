"""One Telegram bot for the whole app (Batch 31, ADR 0019).

Starting a Bot opens a new connection and asks Telegram who it is (getMe):
0.8 to 1 s from Render, measured in Batch 30, and every webhook update, every
reminder run and every background message started one. The app now starts one
at start-up and every sender uses it. Without one (tests, the Action's run),
a sender starts its own, from the Bot its module holds, as before.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telegram import Bot
from telegram.request import HTTPXRequest

from app.config import Settings

logger = logging.getLogger("tm_pro.telegram_bot")

_shared: Bot | None = None


async def start_shared_bot(settings: Settings) -> Bot:
    """Start the app's bot once. Webhook updates, reminders and background
    messages can send at the same time, and one connection, the library's
    default, would make them queue or time out; eight lets them go together."""
    global _shared
    bot = Bot(token=settings.bot_token, request=HTTPXRequest(connection_pool_size=8))
    await bot.initialize()
    _shared = bot
    return bot


async def stop_shared_bot() -> None:
    global _shared
    bot, _shared = _shared, None
    if bot is not None:
        await bot.shutdown()


@asynccontextmanager
async def telegram_bot(settings: Settings, factory=None) -> AsyncIterator[Bot]:
    """The shared bot when the app started one; otherwise a bot of the
    sender's own, made by factory (its module's Bot) and closed afterwards."""
    if _shared is not None:
        yield _shared
        return
    async with (factory or Bot)(token=settings.bot_token) as bot:
        yield bot
