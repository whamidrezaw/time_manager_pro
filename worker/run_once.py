"""
worker/run_once.py — v2
یکبار reminder های سررسیده را پردازش می‌کند و خارج می‌شود.
با timeout برای جلوگیری از هنگ کردن روی GitHub Actions.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from telegram import Bot

from app.config import get_settings
from app.db import close_mongo_connection, connect_to_mongo, ensure_indexes
from app.services.reminders import process_due_reminders

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("tm_pro.run_once")


async def main() -> None:
    logger.info("run_once: connecting to MongoDB (timeout=10s)...")

    try:
        # اتصال با timeout صریح — هنگ نمی‌کند
        await asyncio.wait_for(
            connect_to_mongo(settings),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.error("run_once: MongoDB connection timed out! Check MONGO_URI and Atlas IP whitelist.")
        sys.exit(1)
    except Exception as exc:
        logger.error("run_once: MongoDB connection failed: %s", exc)
        sys.exit(1)

    await ensure_indexes(settings)
    logger.info("run_once: connected. processing reminders...")

    try:
        async with Bot(token=settings.bot_token) as bot:
            processed = await asyncio.wait_for(
                process_due_reminders(bot=bot, settings=settings),
                timeout=60.0,
            )
            logger.info("run_once: done. processed=%s reminders.", processed)
    except asyncio.TimeoutError:
        logger.error("run_once: reminder processing timed out.")
    except Exception as exc:
        logger.error("run_once: error during processing: %s", exc)
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
