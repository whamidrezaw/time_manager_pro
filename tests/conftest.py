from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


# ─── تنظیمات محیط تست ────────────────────────────────────────────────────────
# مهم: MONGO_DB_NAME را همیشه override می‌کنیم (نه setdefault)
# تا حتی اگه .env لود شده باشه، تست‌ها روی DB اصلی اجرا نشن
os.environ["APP_ENV"] = "development"
os.environ["APP_DEBUG"] = "true"
os.environ["MONGO_DB_NAME"] = "time_manager_pro_test"   # ← همیشه override
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["TELEGRAM_INITDATA_MAX_AGE"] = "900"
os.environ["TELEGRAM_INITDATA_FUTURE_SKEW"] = "60"

# این‌ها را فقط اگه set نشده باشن مقداردهی می‌کنیم
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_PYTEST_ONLY")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret-for-pytest-only")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
