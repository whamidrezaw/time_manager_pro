from __future__ import annotations

import asyncio
import logging
import signal

from telegram import Bot

from app.config import get_settings
from app.db import close_mongo_connection, connect_to_mongo, ensure_indexes
from app.services.reminders import process_due_reminders

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("tm_pro.worker")


async def worker_loop() -> None:
    logger.info("Reminder worker starting")

    await connect_to_mongo(settings)
    await ensure_indexes(settings)

    bot = Bot(token=settings.bot_token)

    try:
        async with bot:
            while True:
                try:
                    processed = await process_due_reminders(bot=bot, settings=settings)
                    if processed:
                        logger.info("Processed reminders: %s", processed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Reminder worker iteration failed")

                await asyncio.sleep(settings.reminder_poll_interval_secs)
    finally:
        await close_mongo_connection()
        logger.info("Reminder worker stopped")


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(sig: int, frame: object) -> None:
        logger.info("Reminder worker received signal %s, shutting down gracefully...", sig)
        # همه task های در حال اجرا را cancel می‌کنیم
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # هم SIGTERM (از systemd) و هم SIGINT (از Ctrl+C) را handle می‌کنیم
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        loop.run_until_complete(worker_loop())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Reminder worker stopped cleanly")
    finally:
        # اطمینان از بسته شدن تمیز loop
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()
            logger.info("Event loop closed")


if __name__ == "__main__":
    main()
