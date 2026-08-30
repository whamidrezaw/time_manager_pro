from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from telegram import Bot

from app.config import get_settings
from app.db import close_mongo_connection, connect_to_mongo, ensure_indexes
from app.routes.events import router as events_router
from app.routes.health import router as health_router
from app.routes.telegram import router as telegram_router
from app.routes.web import router as web_router

settings = get_settings()

root_level = getattr(logging, settings.log_level.upper(), logging.INFO)

logging.basicConfig(
    level=root_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if settings.app_env.lower() == "production":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("tm_pro.app")

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)

    try:
        async with Bot(token=settings.bot_token) as bot:
            me = await bot.get_me()
            logger.info("Runtime bot = @%s id=%s", me.username, me.id)

            webhook_url = f"{settings.webapp_base_url}/telegram/webhook"
            await bot.set_webhook(url=webhook_url, secret_token=settings.telegram_webhook_secret)
            logger.info("Telegram webhook set to %s", webhook_url)
    except Exception as exc:
        logger.warning("Runtime bot verification/webhook setup failed: %s", exc)

    await connect_to_mongo(settings)
    await ensure_indexes(settings)

    yield

    await close_mongo_connection()
    logger.info("Stopped %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health_router)
app.include_router(web_router)
app.include_router(events_router)
app.include_router(telegram_router)
