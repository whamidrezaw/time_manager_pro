from __future__ import annotations

import logging
from typing import Optional

import certifi
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.db")

_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError("Database client is not initialized")
    return _client


def get_database() -> AsyncIOMotorDatabase:
    if _database is None:
        raise RuntimeError("Database is not initialized")
    return _database


def get_events_collection() -> AsyncIOMotorCollection:
    return get_database()["events"]


def get_users_collection() -> AsyncIOMotorCollection:
    return get_database()["users"]


async def connect_to_mongo(settings: Settings | None = None) -> AsyncIOMotorDatabase:
    global _client, _database

    if _client is not None and _database is not None:
        return _database

    settings = settings or get_settings()

    _client = AsyncIOMotorClient(
        settings.mongo_uri,
        tlsCAFile=certifi.where(),
    )
    _database = _client[settings.mongo_db_name]

    await _database.command("ping")
    logger.info("MongoDB connected: db=%s", settings.mongo_db_name)

    return _database


async def close_mongo_connection() -> None:
    global _client, _database

    if _client is not None:
        _client.close()
        logger.info("MongoDB connection closed")

    _client = None
    _database = None


async def ensure_indexes(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    events = get_events_collection()

    await events.create_index("expire_at", expireAfterSeconds=0)
    await events.create_index([("notify_status", 1), ("next_notify_at", 1)])
    await events.create_index("user_id")
    await events.create_index([("user_id", 1), ("pinned", -1), ("event_ts_utc", 1)])
    await events.create_index([("user_id", 1), ("category", 1)])
    await events.create_index([("notify_status", 1), ("processing_started_at", 1)])

    rate_limits = get_database()["rate_limits"]
    await rate_limits.create_index("ts", expireAfterSeconds=60)
    await rate_limits.create_index([("user_id", 1), ("bucket", 1)], unique=True)

    logger.info("MongoDB indexes ensured for app=%s", settings.app_name)


async def ping_database() -> bool:
    db = get_database()
    await db.command("ping")
    return True
