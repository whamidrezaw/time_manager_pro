#!/usr/bin/env python3
"""
fix_batch3b.py — TimeManager Pro: event times in the Mini App, plus a fix for
the initData verification change on main.

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch3b.py
    python -m ruff check . ; python -m pytest -q

1. initData verification (urgent — the test suite on main is currently red)

  Commit 92936f5 narrowed the excluded keys to {"hash"}, which left the comment
  above it saying the opposite and broke test_build_data_check_string. More to
  the point, it is a coin flip on production traffic: get it wrong and every
  user of an affected client is locked out with a 403.

  Telegram documents two paths. The bot-token HMAC path builds the
  data-check-string from every received field except `hash`. The third-party
  Ed25519 path excludes `signature` too. Newer clients send a `signature` on
  both, so which string the HMAC covers is genuinely ambiguous from the docs
  alone.

  Rather than guess, verification now accepts either. Both candidates are still
  HMAC-verified against the bot token, so allowing two is exactly as strong as
  allowing one: an attacker who can forge neither string gains nothing from
  there being a second. The second candidate is only computed when a
  `signature` field is actually present.

2. Event times in the Mini App (batch 3b)

  The composer gains an "All-day event" switch. Leave it on and the form works
  exactly as it does today: pick a date, pick a reminder time. Turn it off and
  two fields appear instead — when the event starts, and how long before it you
  want to be told: at the time, 15 or 30 minutes, 1 or 2 hours, a day, a week.

  Event cards, the detail panel and the share text show the time for events
  that have one. All-day events look untouched, because they are.

  This is the front end for the schema shipped in batch 3a. No API change:
  the server has accepted all_day, time_hm and reminders since then.

3. Housekeeping

  fix_batch*.py goes into .gitignore. `git rm --cached` untracks a file but
  leaves it on disk, so the next `git add -A` commits it straight back — which
  is how fix_batch2.py returned once already.

Safe to run twice. Every file is checked against a hash of the version this
script was built against; anything unexpected is reported and left untouched.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
report: list[str] = []


def log(status: str, message: str) -> None:
    report.append(f"  {status:<9} {message}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CONTENT_1 = r'''"""
app/services/auth.py — Fixed v2.0
Fixes:
  - 'signature' field now excluded from data_check_string (was: only 'hash' excluded)
  - Cleaner import order
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request
from pymongo import ReturnDocument

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.auth")

# ── Fallback in-memory rate store (for tests / DB-unavailable) ───────────────
_rate_store: dict[str, list[float]] = {}


def _prune_rate_history(user_id: str, window_seconds: int = 60) -> list[float]:
    now = time.time()
    history = [t for t in _rate_store.get(user_id, []) if now - t < window_seconds]
    _rate_store[user_id] = history
    return history


def _check_rate_limit_memory(user_id: str, settings: Settings) -> None:
    """Fallback in-memory rate limit — only used when MongoDB is unavailable."""
    history = _prune_rate_history(user_id)
    if len(history) >= settings.rate_limit_count:
        logger.warning("Rate limit exceeded (memory fallback): user_id=%s", user_id)
        raise HTTPException(status_code=429, detail="RATE_LIMIT")
    history.append(time.time())
    _rate_store[user_id] = history


def check_rate_limit(user_id: str, settings: Settings | None = None) -> None:
    """Sync version — only used in tests."""
    settings = settings or get_settings()
    _check_rate_limit_memory(user_id, settings)


async def check_rate_limit_mongo(user_id: str, settings: Settings) -> None:
    try:
        from app.db import get_database
        db = get_database()
        rate_coll = db["rate_limits"]

        now = datetime.now(timezone.utc)
        bucket = now.replace(second=0, microsecond=0)

        doc = await rate_coll.find_one_and_update(
            {"user_id": user_id, "bucket": bucket},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {"ts": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        if int(doc.get("count", 0)) > settings.rate_limit_count:
            raise HTTPException(status_code=429, detail="RATE_LIMIT")

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limit mongo unavailable, using memory fallback: %s", exc)
        _check_rate_limit_memory(user_id, settings)


# ── Core auth functions ───────────────────────────────────────────────────────

# Telegram documents two verification paths for initData. The bot-token HMAC
# path builds the data-check-string from every received field except `hash`.
# The third-party Ed25519 path excludes `signature` as well. Newer clients send
# a `signature` field on both paths, which leaves real ambiguity about which
# string the HMAC was computed over — and picking the wrong one locks out every
# user of that client.
#
# So both are accepted. Each candidate is still verified against the bot token,
# so allowing either is exactly as strong as allowing one: an attacker who can
# forge neither string gains nothing from there being two of them.
_ALWAYS_EXCLUDED = frozenset({"hash"})
_SIGNATURE_KEY = "signature"


def build_data_check_string(
    parsed: dict[str, str],
    exclude_signature: bool = False,
) -> str:
    excluded = set(_ALWAYS_EXCLUDED)
    if exclude_signature:
        excluded.add(_SIGNATURE_KEY)
    filtered = {k: v for k, v in parsed.items() if k not in excluded}
    return "\n".join(f"{key}={value}" for key, value in sorted(filtered.items()))


def compute_telegram_hash(
    init_data_map: dict[str, str],
    bot_token: str,
    exclude_signature: bool = False,
) -> str:
    data_check_string = build_data_check_string(init_data_map, exclude_signature)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def hash_matches(init_data_map: dict[str, str], bot_token: str, received_hash: str) -> bool:
    """True when the received hash matches either accepted data-check-string.

    Only tries the second variant when a `signature` field is actually present,
    so the common case still costs one HMAC.
    """
    variants = [False] if _SIGNATURE_KEY not in init_data_map else [False, True]
    return any(
        hmac.compare_digest(
            compute_telegram_hash(init_data_map, bot_token, exclude_signature),
            received_hash,
        )
        for exclude_signature in variants
    )


def parse_init_data(init_data: str) -> dict[str, str]:
    if not init_data:
        raise HTTPException(status_code=403, detail="NO_DATA")

    parsed: dict[str, str] = {}
    for chunk in init_data.split("&"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parsed[key] = unquote(value)

    if not parsed:
        raise HTTPException(status_code=403, detail="INVALID_INIT_DATA")

    return parsed


def parse_init_user(user_raw: str) -> dict[str, Any]:
    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=403, detail="INVALID_USER_JSON") from exc
    if not isinstance(user_data, dict):
        raise HTTPException(status_code=403, detail="INVALID_USER")
    return user_data


def validate_auth_date(
    auth_date_raw: str | None,
    max_age_seconds: int,
    max_future_skew_seconds: int = 60,
    now_ts: int | None = None,
) -> None:
    try:
        auth_date = int(auth_date_raw or "0")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE") from exc

    if auth_date <= 0:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE")

    now = now_ts if now_ts is not None else int(time.time())

    if auth_date < now - max_age_seconds:
        raise HTTPException(status_code=403, detail="EXPIRED")

    if auth_date > now + max_future_skew_seconds:
        raise HTTPException(status_code=403, detail="INVALID_AUTH_DATE")


async def validate_init_data(
    request: Request,
    init_data: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()

    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="MISCONFIGURED")

    parsed = parse_init_data(init_data)

    received_hash = parsed.get("hash")
    if not received_hash:
        raise HTTPException(status_code=403, detail="NO_HASH")

    if not hash_matches(parsed, settings.bot_token, received_hash):
        computed_hash = compute_telegram_hash(parsed, settings.bot_token)
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "Bad Telegram initData HMAC: ip=%s received=%s… computed=%s… "
            "auth_date=%s deploy_marker=TIMEPICKER_CI_BUILD",
            client_ip,
            received_hash[:8],
            computed_hash[:8],
            parsed.get("auth_date"),
        )
        raise HTTPException(status_code=403, detail="BAD_HASH")

    validate_auth_date(
        auth_date_raw=parsed.get("auth_date"),
        max_age_seconds=settings.telegram_initdata_max_age,
        max_future_skew_seconds=settings.telegram_initdata_future_skew,
    )

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=403, detail="NO_USER")

    user_data = parse_init_user(user_raw)
    user_id = str(user_data.get("id", ""))

    if not user_id or not user_id.isdigit():
        raise HTTPException(status_code=403, detail="INVALID_ID")

    await check_rate_limit_mongo(user_id, settings)

    return {
        "user_id": user_id,
        "user": user_data,
        "auth_date": parsed.get("auth_date"),
        "raw": parsed,
    }

async def get_authenticated_user_id(
    request: Request,
    init_data: str,
    settings: Settings | None = None,
) -> str:
    auth_result = await validate_init_data(request, init_data, settings)
    return auth_result["user_id"]
'''

CONTENT_2 = r'''from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.services.auth import (
    build_data_check_string,
    check_rate_limit,
    compute_telegram_hash,
    parse_init_data,
    parse_init_user,
    validate_auth_date,
    validate_init_data,
)


def make_request(ip: str = "127.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_parse_init_data_success() -> None:
    parsed = parse_init_data("query_id=abc&auth_date=123&hash=xyz")
    assert parsed["query_id"] == "abc"
    assert parsed["auth_date"] == "123"
    assert parsed["hash"] == "xyz"


def test_parse_init_data_rejects_empty() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_init_data("")
    assert exc.value.status_code == 403
    assert exc.value.detail == "NO_DATA"


def test_parse_init_user_success() -> None:
    raw = json.dumps({"id": 12345, "first_name": "Ali"})
    parsed = parse_init_user(raw)
    assert parsed["id"] == 12345
    assert parsed["first_name"] == "Ali"


def test_parse_init_user_rejects_invalid_json() -> None:
    with pytest.raises(HTTPException) as exc:
        parse_init_user("{bad json}")
    assert exc.value.status_code == 403
    assert exc.value.detail == "INVALID_USER_JSON"


def test_build_data_check_string_always_excludes_hash() -> None:
    parsed = {
        "auth_date": "111",
        "user": '{"id":1}',
        "hash": "abc",
        "signature": "sig",
        "query_id": "q1",
    }
    result = build_data_check_string(parsed)

    assert "hash=abc" not in result
    assert "auth_date=111" in result
    assert "query_id=q1" in result
    # Default variant follows the bot-token path in Telegram's docs, which
    # excludes only `hash`.
    assert "signature=sig" in result


def test_build_data_check_string_can_also_exclude_signature() -> None:
    parsed = {"auth_date": "111", "hash": "abc", "signature": "sig"}
    result = build_data_check_string(parsed, exclude_signature=True)

    assert "signature=" not in result
    assert "auth_date=111" in result


def test_hash_matches_accepts_either_data_check_string() -> None:
    """Whichever variant Telegram's client actually signed, verification passes;
    both are checked against the bot token, so neither can be forged."""
    from app.services.auth import compute_telegram_hash, hash_matches

    token = "123456:TEST"
    parsed = {"auth_date": "111", "user": '{"id":1}', "signature": "sig"}

    with_signature = compute_telegram_hash(parsed, token, exclude_signature=False)
    without_signature = compute_telegram_hash(parsed, token, exclude_signature=True)

    assert with_signature != without_signature
    assert hash_matches(parsed, token, with_signature)
    assert hash_matches(parsed, token, without_signature)
    assert not hash_matches(parsed, token, "0" * 64)


def test_hash_matches_rejects_a_wrong_token() -> None:
    from app.services.auth import compute_telegram_hash, hash_matches

    parsed = {"auth_date": "111", "signature": "sig"}
    genuine = compute_telegram_hash(parsed, "123456:TEST")

    assert not hash_matches(parsed, "999999:OTHER", genuine)


def test_compute_telegram_hash_matches_manual_hmac() -> None:
    parsed = {
        "auth_date": "111",
        "query_id": "q1",
        "user": '{"id":1}',
    }
    token = "test_bot_token"

    data_check_string = "auth_date=111\nquery_id=q1\nuser={\"id\":1}"
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    actual = compute_telegram_hash(parsed, token)
    assert actual == expected


def test_validate_auth_date_accepts_recent_value() -> None:
    validate_auth_date(
        auth_date_raw="1000",
        max_age_seconds=900,
        max_future_skew_seconds=60,
        now_ts=1050,
    )


def test_validate_auth_date_rejects_old_value() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_auth_date(
            auth_date_raw="1000",
            max_age_seconds=10,
            max_future_skew_seconds=60,
            now_ts=2000,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "EXPIRED"


def test_validate_auth_date_rejects_far_future_value() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_auth_date(
            auth_date_raw="5000",
            max_age_seconds=900,
            max_future_skew_seconds=60,
            now_ts=1000,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "INVALID_AUTH_DATE"


def test_check_rate_limit_allows_under_limit() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()
    check_rate_limit("1", settings)
    assert "1" in auth_module._rate_store


def test_check_rate_limit_blocks_when_limit_reached() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()

    # Must be RECENT timestamps — the real rate limiter correctly prunes
    # anything older than the 60s window before counting, so seeding with
    # ancient timestamps (e.g. 1.0) makes this test pass even when the real
    # limiter is broken, and fail even when it's working correctly.
    now = time.time()
    auth_module._rate_store["99"] = [now - 1] * settings.rate_limit_count
    with pytest.raises(HTTPException) as exc:
        check_rate_limit("99", settings)

    assert exc.value.status_code == 429
    assert exc.value.detail == "RATE_LIMIT"


@pytest.mark.anyio
async def test_validate_init_data_success() -> None:
    from app.services import auth as auth_module

    auth_module._rate_store.clear()
    settings = get_settings()

    user_json = json.dumps({"id": 123456, "first_name": "Test"})
    parsed = {
        "auth_date": "1000",
        "query_id": "AAEAAQ",
        "user": user_json,
    }
    parsed["hash"] = compute_telegram_hash(parsed, settings.bot_token)

    init_data = (
        f"auth_date={parsed['auth_date']}"
        f"&query_id={parsed['query_id']}"
        f"&user={user_json}"
        f"&hash={parsed['hash']}"
    )

    import app.services.auth as auth_service

    original_time = auth_service.time.time
    auth_service.time.time = lambda: 1050
    try:
        result = await validate_init_data(make_request(), init_data, settings)
    finally:
        auth_service.time.time = original_time

    assert result["user_id"] == "123456"
    assert result["user"]["first_name"] == "Test"


@pytest.mark.anyio
async def test_validate_init_data_rejects_bad_hash() -> None:
    settings = get_settings()
    user_json = json.dumps({"id": 123456})

    init_data = (
        f"auth_date=1000"
        f"&query_id=AAEAAQ"
        f"&user={user_json}"
        f"&hash=bad_hash"
    )

    import app.services.auth as auth_service

    original_time = auth_service.time.time
    auth_service.time.time = lambda: 1050
    try:
        with pytest.raises(HTTPException) as exc:
            await validate_init_data(make_request(), init_data, settings)
    finally:
        auth_service.time.time = original_time

    assert exc.value.status_code == 403
    assert exc.value.detail == "BAD_HASH"
'''

CONTENT_3 = r'''<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#5b6cf8">
  <meta name="color-scheme" content="light dark">
  <meta name="description" content="TimeManager Pro — Smart event reminders with Gregorian and Jalali dates for Telegram.">
  <title>TimeManager Pro</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css?v={{ asset_version }}">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
</head>
<body>
  <a href="#eventList" class="sr-only">Skip to content</a>

  <noscript>
    <div class="noscript-box">
      <div class="noscript-icon">📅</div>
      <p><strong>JavaScript Required</strong></p>
      <p class="noscript-sub">Please enable JavaScript to use TimeManager Pro.</p>
    </div>
  </noscript>

  <!-- ── Header ─────────────────────────────────────── -->
  <header class="app-header">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="4" width="18" height="17" rx="3" stroke="currentColor" stroke-width="2"/>
          <path d="M3 9h18" stroke="currentColor" stroke-width="2"/>
          <path d="M8 2v4M16 2v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <circle cx="12" cy="15" r="2" fill="currentColor"/>
        </svg>
      </div>
      <div class="brand-copy">
        <strong class="brand-title">TimeManager Pro</strong>
        <span class="brand-subtitle">Smart reminders in Telegram</span>
      </div>
    </div>
    <button type="button" id="refreshBtn" class="icon-btn" aria-label="Refresh events" title="Refresh">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
      </svg>
    </button>
  </header>

  <!-- ── Main Content ───────────────────────────────── -->
  <main id="eventList" class="app-main">

    <!-- Hero Card -->
    <section class="hero-card">
      <div class="hero-left">
        <div class="hero-badge">✨ Your Personal Planner</div>
        <h1 class="hero-title">Stay on top of every moment</h1>
        <p class="hero-text">Save events, birthdays & tasks — get reminders directly in Telegram.</p>
      </div>
      <div class="hero-stats">
        <div class="stat-box">
          <span class="stat-icon">📅</span>
          <strong id="eventCount" class="stat-value">0</strong>
          <span class="stat-label">Events</span>
        </div>
        <div class="stat-box">
          <span class="stat-icon">🔔</span>
          <strong id="syncStatus" class="stat-value">Ready</strong>
          <span class="stat-label">Status</span>
        </div>
      </div>
    </section>

    <!-- Toolbar -->
    <section class="toolbar" aria-label="Filters and search">
      <div class="toolbar-row" id="filterRow" role="group" aria-label="Category filter">
        <button type="button" class="seg-btn is-active" data-filter="all">🌐 All</button>
        <button type="button" class="seg-btn" data-filter="pinned">📌 Pinned</button>
        <button type="button" class="seg-btn" data-filter="birthday">🎂 Birthday</button>
        <button type="button" class="seg-btn" data-filter="work">💼 Work</button>
        <button type="button" class="seg-btn" data-filter="health">❤️ Health</button>
        <button type="button" class="seg-btn" data-filter="family">👨‍👩‍👧 Family</button>
        <button type="button" class="seg-btn" data-filter="travel">✈️ Travel</button>
        <button type="button" class="seg-btn" data-filter="finance">💰 Finance</button>
        <button type="button" class="seg-btn" data-filter="study">📚 Study</button>
      </div>
      <label class="search-wrap" for="searchInput">
        <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input type="search" id="searchInput" class="search-input" placeholder="Search events…" autocomplete="off" inputmode="search">
      </label>
    </section>

    <!-- Skeleton (hidden by default, shown while loading) -->
    <section id="skeletonState" class="skeleton-list" hidden>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div></div>
    </section>

    <!-- Empty State -->
    <section id="listState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle">
          <span class="empty-emoji">🗓️</span>
        </div>
        <div class="empty-dots">
          <span></span><span></span><span></span>
        </div>
      </div>
      <h2 class="empty-state-title">No events yet!</h2>
      <p class="empty-state-text">Add your first event and start receiving smart reminders directly in Telegram.</p>
      <button type="button" id="emptyAddBtn" class="btn-primary btn-cta">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        Add your first event
      </button>
      <div class="empty-hints">
        <div class="hint-chip">🎂 Birthdays</div>
        <div class="hint-chip">💼 Meetings</div>
        <div class="hint-chip">❤️ Appointments</div>
        <div class="hint-chip">✈️ Travel</div>
      </div>
    </section>

    <!-- Error State -->
    <section id="listErrorState" class="empty-state is-error" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle is-error-circle"><span class="empty-emoji">⚠️</span></div>
      </div>
      <h2 class="empty-state-title">Something went wrong</h2>
      <p class="empty-state-text">Could not connect to the server. Please check your connection and try again.</p>
      <button type="button" id="retryBtn" class="btn-primary">Try again</button>
    </section>

    <!-- No Results State -->
    <section id="noResultsState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle"><span class="empty-emoji">🔍</span></div>
      </div>
      <h2 class="empty-state-title">No results found</h2>
      <p class="empty-state-text">Try a different search term or filter.</p>
    </section>

    <!-- Events List -->
    <section id="eventsWrap" class="events-wrap" aria-live="polite" aria-label="Event list"></section>

    <!-- Load More -->
    <div id="loadMoreWrap" class="load-more-wrap" hidden>
      <button type="button" id="loadMoreBtn" class="btn-secondary btn-load-more">Load more events</button>
    </div>
  </main>

  <!-- ── Floating Add Button ────────────────────────── -->
  <button type="button" id="openComposerBtn" class="floating-add-btn" aria-label="Add event" aria-controls="composerSheet" aria-expanded="false">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
    <span>Add Event</span>
  </button>

  <!-- ── Sheet Overlay ──────────────────────────────── -->
  <div id="sheetOverlay" class="sheet-overlay" hidden aria-hidden="true"></div>

  <!-- ── Composer Sheet ─────────────────────────────── -->
  <section id="composerSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="composerTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="composerTitle" class="sheet-title">New Event</h2>
        <p id="composerSubtitle" class="sheet-subtitle">Set title, date and repeat pattern.</p>
      </div>
      <button type="button" id="closeComposerX" class="icon-btn" aria-label="Close form">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <form id="eventForm" class="sheet-form" novalidate>
      <input type="hidden" id="eventId">

      <div class="field-group">
        <label class="field-label" for="title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Event Title
        </label>
        <input type="text" id="title" class="field-input" placeholder="e.g. Mom's Birthday" maxlength="200" autocomplete="off" required>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="date">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            Gregorian Date
          </label>
          <input type="date" id="date" class="field-input" required>
        </div>
        <div class="field-group">
          <label class="field-label" for="date-jalali">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
            Jalali Date
          </label>
          <input type="text" id="date-jalali" class="field-input" placeholder="1405/01/31" maxlength="10" inputmode="numeric" autocomplete="off" dir="ltr">
        </div>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="repeat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
            Repeat
          </label>
          <select id="repeat" class="field-input field-select">
            <option value="none">One time</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="field-group" id="repeatUntilWrap" hidden>
          <label class="field-label" for="repeatUntil">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
            Repeat Until
            <span class="field-optional">(optional)</span>
          </label>
          <input type="date" id="repeatUntil" class="field-input">
        </div>
        <div class="field-group">
          <label class="field-label" for="category">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
            Category
          </label>
          <select id="category" class="field-input field-select">
            <option value="general">🌐 General</option>
            <option value="birthday">🎂 Birthday</option>
            <option value="work">💼 Work</option>
            <option value="family">👨‍👩‍👧 Family</option>
            <option value="health">❤️ Health</option>
            <option value="travel">✈️ Travel</option>
            <option value="finance">💰 Finance</option>
            <option value="study">📚 Study</option>
            <option value="other">📌 Other</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="allDay">
        <input type="checkbox" id="allDay" checked>
        <span class="check-label">
          <span class="check-icon">📆</span>
          All-day event
        </span>
      </label>

      <div class="grid-2">
        <div class="field-group" id="eventTimeWrap" hidden>
          <label class="field-label" for="eventTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            Event Time
          </label>
          <input type="time" id="eventTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderTimeWrap">
          <label class="field-label" for="reminderTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Reminder Time
          </label>
          <input type="time" id="reminderTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderOffsetWrap" hidden>
          <label class="field-label" for="reminderOffset">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Remind Me
          </label>
          <select id="reminderOffset" class="field-input field-select">
            <option value="0">At time of event</option>
            <option value="15">15 minutes before</option>
            <option value="30">30 minutes before</option>
            <option value="60" selected>1 hour before</option>
            <option value="120">2 hours before</option>
            <option value="1440">1 day before</option>
            <option value="10080">1 week before</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="pin">
        <input type="checkbox" id="pin">
        <span class="check-label">
          <span class="check-icon">📌</span>
          Pin this event to the top
        </span>
      </label>

      <div class="field-group">
        <label class="field-label" for="note">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
          Note <span class="field-optional">(optional)</span>
        </label>
        <textarea id="note" class="field-input field-textarea" rows="4" maxlength="2000" placeholder="Add details, tasks, or a checklist…"></textarea>
        <span class="char-count" id="noteCharCount">0 / 2000</span>
      </div>

      <div class="form-actions">
        <button type="button" id="cancelBtn" class="btn-secondary">Cancel</button>
        <button type="submit" id="saveEventBtn" class="btn-primary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Save Event
        </button>
      </div>
    </form>
  </section>

  <!-- ── Detail Sheet ───────────────────────────────── -->
  <section id="detailSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="detailTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="detailTitle" class="sheet-title">Event Details</h2>
        <p id="detailSubtitle" class="sheet-subtitle">Full view and actions</p>
      </div>
      <button type="button" id="closeDetailX" class="icon-btn" aria-label="Close details">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <article class="detail-card">
      <div class="detail-topline">
        <span id="detailCategoryBadge" class="badge">General</span>
        <span id="detailRepeatBadge" class="badge badge-muted">One time</span>
        <span id="detailPinnedBadge" class="badge badge-pin" hidden>📌 Pinned</span>
      </div>

      <h3 id="detailEventTitle" class="detail-event-title">—</h3>

      <!-- Countdown Ring -->
      <div class="countdown-ring-wrap" id="countdownRingWrap">
        <div class="countdown-ring" id="countdownRing">
          <span id="countdownDays" class="ring-days">—</span>
          <span class="ring-label">days</span>
        </div>
        <div id="detailCountdownText" class="countdown-ring-text">—</div>
      </div>

      <div class="detail-meta-grid">
        <div class="detail-meta-box">
          <span class="detail-meta-label">📅 Gregorian</span>
          <strong id="detailDateIso">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🗓️ Jalali</span>
          <strong id="detailDateJalali">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🌍 Timezone</span>
          <strong id="detailTimezone">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🔔 Status</span>
          <strong id="detailStatus">—</strong>
        </div>
      </div>

      <div class="detail-actions">
        <button type="button" id="detailEditBtn" class="btn-action btn-action-edit">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Edit
        </button>
        <button type="button" id="detailShareBtn" class="btn-action btn-action-share">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
          Share
        </button>
        <button type="button" id="detailPinBtn" class="btn-action btn-action-pin">📌 Pin</button>
        <button type="button" id="detailDeleteBtn" class="btn-action btn-action-delete">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          Delete
        </button>
      </div>

      <div class="field-group">
        <label class="field-label" for="detailNote">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          Event Note
        </label>
        <textarea id="detailNote" class="field-input field-textarea" rows="6" maxlength="2000" placeholder="Write a note, checklist, or details…"></textarea>
      </div>

      <div class="form-actions">
        <button type="button" id="detailNoteCancelBtn" class="btn-secondary">Reset</button>
        <button type="button" id="detailNoteSaveBtn" class="btn-primary">Save Note</button>
      </div>
    </article>
  </section>

  <!-- ── First-run Onboarding ──────────────────────── -->
  <div id="onboardingOverlay" class="confirm-overlay onboarding-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">
      <div class="confirm-icon" id="onboardingIcon">🗓️</div>
      <h3 id="onboardingTitle" class="confirm-title">Never miss what matters</h3>
      <p id="onboardingText" class="confirm-text">Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.</p>
      <div class="onboarding-dots" id="onboardingDots" aria-hidden="true">
        <span class="is-active"></span><span></span><span></span>
      </div>
      <div class="confirm-actions onboarding-actions">
        <button type="button" id="onboardingSkipBtn" class="btn-secondary">Skip</button>
        <button type="button" id="onboardingNextBtn" class="btn-primary">Next</button>
      </div>
    </div>
  </div>

  <!-- ── Custom Confirm Dialog ──────────────────────── -->
  <div id="confirmOverlay" class="confirm-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle">
      <div class="confirm-icon" id="confirmIcon">🗑️</div>
      <h3 id="confirmTitle" class="confirm-title">Delete Event?</h3>
      <p id="confirmText" class="confirm-text">This action cannot be undone.</p>
      <div class="confirm-actions">
        <button type="button" id="confirmCancelBtn" class="btn-secondary">Cancel</button>
        <button type="button" id="confirmOkBtn" class="btn-danger">Delete</button>
      </div>
    </div>
  </div>

  <!-- ── Toast ─────────────────────────────────────── -->
  <div id="toast" class="toast" role="status" aria-live="polite" aria-atomic="true"></div>

  <script src="/static/app.js?v={{ asset_version }}" defer></script>
</body>
</html>
'''

CONTENT_4 = r'''/**
 * TimeManager Pro — app.js v2.0
 * Fixes applied:
 *  - event_id (was: eventid) in edit/delete/pin/note payloads
 *  - All countdown text translated to English (was: Persian)
 *  - window.confirm replaced with custom confirm dialog
 *  - All debug console.log removed
 *  - API field names: date_iso / date_jalali / notify_status / tz_name
 *  - Pinned badge text: "Pinned" (was: "سنجاق‌شده")
 *  - Skeleton loading state
 *  - Pagination / load-more
 *  - Countdown ring in detail view
 *  - Note character counter
 *  - Reminder hour field support
 *  - Reminder hour actually sent to the backend (was: silently dropped)
 *  - Haptic feedback on save/delete/pin/error (via Telegram WebApp SDK)
 *  - First-run onboarding overlay (3 steps, shown once via localStorage)
 */

(() => {
  "use strict";

  /* ── Telegram WebApp ────────────────────────────────── */
  const tg = window.Telegram?.WebApp || null;

  function fatal(message) {
    document.body.innerHTML = `
      <div style="padding:40px 20px;text-align:center;font-family:system-ui,sans-serif;">
        <div style="font-size:2.5rem;margin-bottom:16px;">⚠️</div>
        <h2 style="margin:0 0 12px;font-size:1.2rem;">Something went wrong</h2>
        <p style="color:#666;margin:0;">${String(message).replace(/</g, "&lt;")}</p>
      </div>
    `;
  }

  if (!tg) {
    fatal("This application only works inside Telegram. Please open it via the Telegram Mini App.");
    return;
  }

  try { tg.ready(); tg.expand(); } catch (_) {}

  const initData = tg.initData || "";

  if (!initData) {
    fatal("Telegram Mini App could not authenticate. Please reopen the app from Telegram.");
    return;
  }

  /* ── App State ──────────────────────────────────────── */
  const state = {
    events: [],
    filteredEvents: [],
    currentFilter: "all",
    searchTerm: "",
    activeSheet: null,
    detailEventId: null,
    editingEventId: null,
    lastFocusedElement: null,
    skip: 0,
    hasMore: false,
    isLoading: false,
    initData,
  };

  /* ── Element Refs ───────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const els = {
    syncStatus:         $("syncStatus"),
    eventCount:         $("eventCount"),
    refreshBtn:         $("refreshBtn"),
    retryBtn:           $("retryBtn"),
    emptyAddBtn:        $("emptyAddBtn"),
    onboardingOverlay:  $("onboardingOverlay"),
    onboardingIcon:     $("onboardingIcon"),
    onboardingTitle:    $("onboardingTitle"),
    onboardingText:     $("onboardingText"),
    onboardingDots:     $("onboardingDots"),
    onboardingSkipBtn:  $("onboardingSkipBtn"),
    onboardingNextBtn:  $("onboardingNextBtn"),
    repeatUntilWrap:    $("repeatUntilWrap"),
    repeatUntil:        $("repeatUntil"),
    searchInput:        $("searchInput"),
    filterButtons:      $$("[data-filter]"),
    eventsWrap:         $("eventsWrap"),
    listState:          $("listState"),
    listErrorState:     $("listErrorState"),
    noResultsState:     $("noResultsState"),
    skeletonState:      $("skeletonState"),
    loadMoreWrap:       $("loadMoreWrap"),
    loadMoreBtn:        $("loadMoreBtn"),
    toast:              $("toast"),

    // Composer
    openComposerBtn:    $("openComposerBtn"),
    closeComposerX:     $("closeComposerX"),
    cancelBtn:          $("cancelBtn"),
    saveEventBtn:       $("saveEventBtn"),
    composerSheet:      $("composerSheet"),
    composerTitle:      $("composerTitle"),
    composerSubtitle:   $("composerSubtitle"),
    eventForm:          $("eventForm"),
    eventId:            $("eventId"),
    title:              $("title"),
    date:               $("date"),
    dateJalali:         $("date-jalali"),
    repeat:             $("repeat"),
    category:           $("category"),
    pin:                $("pin"),
    note:               $("note"),
    noteCharCount:      $("noteCharCount"),
    allDay:             $("allDay"),
    eventTimeWrap:      $("eventTimeWrap"),
    eventTime:          $("eventTime"),
    reminderTimeWrap:   $("reminderTimeWrap"),
    reminderTime:       $("reminderTime"),
    reminderOffsetWrap: $("reminderOffsetWrap"),
    reminderOffset:     $("reminderOffset"),

    // Detail
    detailSheet:        $("detailSheet"),
    closeDetailX:       $("closeDetailX"),
    detailEditBtn:      $("detailEditBtn"),
    detailShareBtn:     $("detailShareBtn"),
    detailPinBtn:       $("detailPinBtn"),
    detailDeleteBtn:    $("detailDeleteBtn"),
    detailNote:         $("detailNote"),
    detailNoteSaveBtn:  $("detailNoteSaveBtn"),
    detailNoteCancelBtn:$("detailNoteCancelBtn"),
    detailEventTitle:   $("detailEventTitle"),
    detailCategoryBadge:$("detailCategoryBadge"),
    detailRepeatBadge:  $("detailRepeatBadge"),
    detailPinnedBadge:  $("detailPinnedBadge"),
    detailDateIso:      $("detailDateIso"),
    detailDateJalali:   $("detailDateJalali"),
    detailTimezone:     $("detailTimezone"),
    detailStatus:       $("detailStatus"),
    countdownRing:      $("countdownRing"),
    countdownDays:      $("countdownDays"),
    detailCountdownText:$("detailCountdownText"),

    // Confirm dialog
    confirmOverlay:     $("confirmOverlay"),
    confirmTitle:       $("confirmTitle"),
    confirmText:        $("confirmText"),
    confirmOkBtn:       $("confirmOkBtn"),
    confirmCancelBtn:   $("confirmCancelBtn"),

    sheetOverlay:       $("sheetOverlay"),
  };

  /* ── Label Maps ─────────────────────────────────────── */
  const CATEGORY_LABELS = {
    general: "🌐 General",  birthday: "🎂 Birthday",
    work:    "💼 Work",      family:   "👨‍👩‍👧 Family",
    health:  "❤️ Health",   travel:   "✈️ Travel",
    finance: "💰 Finance",  study:    "📚 Study",
    other:   "📌 Other",
  };

  const CATEGORY_PLAIN = {
    general: "General",  birthday: "Birthday",
    work:    "Work",      family:   "Family",
    health:  "Health",   travel:   "Travel",
    finance: "Finance",  study:    "Study",
    other:   "Other",
  };

  const REPEAT_LABELS = {
    none: "One time", daily: "🔁 Daily",
    weekly: "🔁 Weekly", monthly: "🔁 Monthly", yearly: "🎂 Yearly",
  };

  const STATUS_LABELS = {
    pending: "Pending", processing: "Processing...",
    done: "✅ Sent", failed: "❌ Failed",
  };

  /* ── Telegram Theme ─────────────────────────────────── */
  // Telegram themeParams key -> CSS custom property read by style.css.
  // Every one of these has a fallback in the stylesheet, so a client that
  // sends only half of them still renders correctly.
  const TG_THEME_MAP = {
    bg_color:                "--tg-bg",
    secondary_bg_color:      "--tg-bg-2",
    section_bg_color:        "--tg-surface",
    text_color:              "--tg-text",
    subtitle_text_color:     "--tg-text-2",
    hint_color:              "--tg-text-muted",
    section_separator_color: "--tg-border",
    link_color:              "--tg-link",
    destructive_text_color:  "--tg-danger",
  };

  function initTelegram() {
    try {
      applyTelegramTheme();
      if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("secondary_bg_color");
      tg.onEvent?.("themeChanged", applyTelegramTheme);
    } catch (_) {}
  }

  function applyTelegramTheme() {
    const root = document.documentElement;
    const params = tg?.themeParams || {};

    Object.entries(TG_THEME_MAP).forEach(([key, cssVar]) => {
      const value = params[key];
      if (typeof value === "string" && value.trim()) {
        root.style.setProperty(cssVar, value.trim());
      } else {
        // Client didn't send this one — drop back to the stylesheet default
        // instead of keeping a stale value from the previous theme.
        root.style.removeProperty(cssVar);
      }
    });

    // Inside the Telegram WebView tg.colorScheme is authoritative:
    // prefers-color-scheme reports the OS setting, which can disagree with
    // the theme the user actually chose in Telegram.
    root.setAttribute("data-tg-scheme", tg?.colorScheme === "dark" ? "dark" : "light");
  }

  /* ── Loading / Status ───────────────────────────────── */
  function setLoading(on) {
    state.isLoading = on;
    document.body.classList.toggle("is-loading", on);
    if (els.syncStatus) els.syncStatus.textContent = on ? "Syncing…" : "Ready";
  }

  function setSkeleton(on) {
    if (els.skeletonState) els.skeletonState.hidden = !on;
  }

  /* ── Toast ──────────────────────────────────────────── */
  let _toastTimer = null;
  function showToast(message, type = "info") {
    if (!els.toast) return;
    els.toast.innerHTML = `
      ${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"} ${escapeHtml(message)}
    `;
    els.toast.dataset.type = type;
    els.toast.classList.add("is-visible");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => els.toast.classList.remove("is-visible"), 2800);

    // Native-feeling haptic nudge on meaningful outcomes (skip routine "info" toasts
    // so this stays purposeful rather than buzzing on everything).
    try {
      if (type === "success") tg?.HapticFeedback?.notificationOccurred?.("success");
      else if (type === "error") tg?.HapticFeedback?.notificationOccurred?.("error");
    } catch (_) {}
  }

  /* ── Custom Confirm Dialog ──────────────────────────── */
  function showConfirm({ title, text, okLabel = "Confirm", icon = "🗑️" }) {
    return new Promise((resolve) => {
      if (!els.confirmOverlay) { resolve(true); return; }

      if (els.confirmTitle) els.confirmTitle.textContent = title;
      if (els.confirmText)  els.confirmText.textContent  = text;
      if (els.confirmOkBtn) els.confirmOkBtn.textContent = okLabel;
      const iconEl = els.confirmOverlay.querySelector(".confirm-icon");
      if (iconEl) iconEl.textContent = icon;

      els.confirmOverlay.hidden = false;
      els.confirmOverlay.removeAttribute("aria-hidden");

      const cleanup = (result) => {
        els.confirmOverlay.hidden = true;
        els.confirmOverlay.setAttribute("aria-hidden", "true");
        resolve(result);
      };

      const handleOk     = () => cleanup(true);
      const handleCancel = () => cleanup(false);
      const handleKey    = (e) => { if (e.key === "Escape") cleanup(false); };

      els.confirmOkBtn?.addEventListener("click", handleOk, { once: true });
      els.confirmCancelBtn?.addEventListener("click", handleCancel, { once: true });
      document.addEventListener("keydown", handleKey, { once: true });
    });
  }

  /* ── API ────────────────────────────────────────────── */
  async function apiPost(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: state.initData, ...payload }),
    });

    let data = null;
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      const detail = data?.detail || "REQUEST_FAILED";
      throw new Error(detail);
    }
    return data;
  }

  function normalizeError(error) {
    const map = {
      NO_DATA:                "Telegram authentication data is missing.",
      BAD_HASH:               "The Telegram request signature is invalid.",
      EXPIRED:                "Your session has expired. Please reopen the Mini App.",
      INVALID_DATE:           "The date entered is not valid.",
      TITLE_TOO_LONG:         "The title is too long (max 200 chars).",
      NOTE_TOO_LONG:          "The note is too long (max 2000 chars).",
      EVENT_LIMIT_REACHED:    "You have reached the maximum number of events (500).",
      RATE_LIMIT:             "Too many requests. Please slow down.",
      NOT_FOUND_OR_UNAUTHORIZED: "Event not found or access denied.",
      REQUEST_FAILED:         "The request failed. Please try again.",
      NO_HASH:                "Telegram authentication data is incomplete.",
      NO_USER:                "User information was not received from Telegram.",
      INVALID_AUTH_DATE:      "Authentication timestamp is invalid.",
      MISCONFIGURED:          "Server configuration error. Please contact support.",
      INVALID_ID_FORMAT:      "Invalid event ID.",
    };
    const detail = error?.message || "";
    return map[detail] || `Error: ${detail || "Unknown error"}`;
  }

  /* ── Load Events ─────────────────────────────────────── */
  async function loadEvents(append = false) {
    if (!append) {
      state.skip = 0;
      setSkeleton(true);
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = true;
      if (els.noResultsState) els.noResultsState.hidden = true;
      if (els.loadMoreWrap) els.loadMoreWrap.hidden = true;
    }

    setLoading(true);
    try {
      const data = await apiPost("/api/list", { skip: state.skip });
      const newItems = Array.isArray(data.targets) ? data.targets : [];
      state.hasMore = !!data.has_more;

      if (append) {
        state.events = [...state.events, ...newItems];
      } else {
        state.events = newItems;
      }

      state.skip = state.events.length;
      applyFilters();
      renderEvents();
      updateCounters();
      showStatePanel();

      if (els.loadMoreWrap) els.loadMoreWrap.hidden = !state.hasMore;
    } catch (error) {
      state.events = [];
      state.filteredEvents = [];
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = false;
      showToast(normalizeError(error), "error");
      if (els.syncStatus) els.syncStatus.textContent = "Error";
    } finally {
      setSkeleton(false);
      setLoading(false);
    }
  }

  function updateCounters() {
    if (els.eventCount) els.eventCount.textContent = String(state.events.length);
  }

  /* ── Filters ────────────────────────────────────────── */
  function applyFilters() {
    const q = state.searchTerm.trim().toLowerCase();
    state.filteredEvents = state.events.filter((item) => {
      const matchFilter =
        state.currentFilter === "all"    ? true :
        state.currentFilter === "pinned" ? item.pinned :
        item.category === state.currentFilter;

      // Search over all text fields
      const haystack = [
        item.title, item.note, item.date_iso, item.date_jalali,
        CATEGORY_PLAIN[item.category] || "",
      ].join(" ").toLowerCase();

      return matchFilter && (!q || haystack.includes(q));
    });
  }

  /* ── State Panel ────────────────────────────────────── */
  function showStatePanel() {
    const hasEvents   = state.events.length > 0;
    const hasFiltered = state.filteredEvents.length > 0;

    if (els.listState)     els.listState.hidden = true;
    if (els.listErrorState) els.listErrorState.hidden = true;
    if (els.noResultsState) els.noResultsState.hidden = true;

    if (!hasEvents) {
      if (els.listState) els.listState.hidden = false;
    } else if (hasEvents && !hasFiltered) {
      if (els.noResultsState) els.noResultsState.hidden = false;
    }
  }

  /* ── Countdown Logic (English only) ─────────────────── */
  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addMonthsSafe(date, n) {
    const d = new Date(date.getFullYear(), date.getMonth(), 1);
    d.setMonth(d.getMonth() + n);
    const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(date.getDate(), lastDay));
    return d;
  }

  function diffParts(from, to) {
    let cursor = startOfDay(from);
    const target = startOfDay(to);
    const totalMs = target - cursor;

    if (totalMs < 0) {
      return { past: true, totalDays: Math.ceil(-totalMs / 86400000) };
    }

    const totalDays = Math.ceil(totalMs / 86400000);

    let years = 0, months = 0;
    while (addMonthsSafe(cursor, 12) <= target) { years++;  cursor = addMonthsSafe(cursor, 12); }
    while (addMonthsSafe(cursor, 1)  <= target) { months++; cursor = addMonthsSafe(cursor, 1); }

    const remDays = Math.ceil((target - cursor) / 86400000);
    const weeks = Math.floor(remDays / 7);
    const days  = remDays % 7;

    return { past: false, years, months, weeks, days, totalDays };
  }

  function pluralize(n, word) {
    return `${n} ${word}${n !== 1 ? "s" : ""}`;
  }

  function getCountdownData(dateIso) {
    if (!dateIso) return { tone: "long", shortText: "—", fullText: "—", totalDays: 999 };

    const today = startOfDay(new Date());
    const target = startOfDay(new Date(`${dateIso}T00:00:00`));
    const diff = diffParts(today, target);

    if (diff.past) {
      return {
        tone: "past",
        shortText: `${pluralize(diff.totalDays, "day")} ago`,
        fullText: `This event was ${pluralize(diff.totalDays, "day")} ago`,
        totalDays: -diff.totalDays,
      };
    }

    if (diff.totalDays === 0) {
      return {
        tone: "today",
        shortText: "Today! 🎉",
        fullText: "This event is today!",
        totalDays: 0,
      };
    }

    // Build human-readable parts
    const parts = [];
    if (diff.years)  parts.push(pluralize(diff.years, "year"));
    if (diff.months) parts.push(pluralize(diff.months, "month"));
    if (diff.weeks)  parts.push(pluralize(diff.weeks, "week"));
    if (diff.days)   parts.push(pluralize(diff.days, "day"));

    const shortParts = [];
    if (diff.years)  shortParts.push(pluralize(diff.years, "yr"));
    if (diff.months) shortParts.push(pluralize(diff.months, "mo"));
    const extraDays = diff.weeks * 7 + diff.days;
    if (extraDays)   shortParts.push(pluralize(extraDays, "day"));

    const fullText  = `${parts.join(", ")} remaining`;
    const shortText = `${shortParts.join(" ")} left`;

    let tone = "long";
    if      (diff.totalDays <= 3)   tone = "critical";
    else if (diff.totalDays <= 7)   tone = "critical";
    else if (diff.totalDays <= 30)  tone = "soon";
    else if (diff.totalDays <= 90)  tone = "warm";
    else if (diff.totalDays <= 180) tone = "cool";
    else if (diff.totalDays <= 365) tone = "future";

    return { tone, shortText, fullText, totalDays: diff.totalDays };
  }

  /* ── Render Events ───────────────────────────────────── */
  function renderEvents() {
    if (!els.eventsWrap) return;

    if (!state.filteredEvents.length) {
      els.eventsWrap.innerHTML = "";
      showStatePanel();
      return;
    }

    const frag = document.createDocumentFragment();

    state.filteredEvents.forEach((event) => {
      const cd = getCountdownData(event.date_iso);
      const catClass = `cat-${event.category || "general"}`;
      const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";
      const repeatLabel = REPEAT_LABELS[event.repeat] || "One time";

      const art = document.createElement("article");
      art.className = `event-card ${catClass}`;
      art.tabIndex = 0;
      art.setAttribute("role", "button");
      art.setAttribute("aria-label", `Open details for ${event.title}`);
      art.dataset.id = event.id;

      // Progress bar: how close to event (cap at 365 days)
      const progressPct = cd.totalDays <= 0
        ? 100
        : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));

      art.innerHTML = `
        <div class="event-card-top">
          <div class="event-head">
            <h3 class="event-title">${escapeHtml(event.title)}</h3>
            <div class="event-badges">
              ${event.pinned ? '<span class="badge badge-pin">📌 Pinned</span>' : ""}
              <span class="badge ${getCatBadgeClass(event.category)}">${escapeHtml(catLabel)}</span>
              <span class="urgency-badge urgency-${cd.tone}">${escapeHtml(cd.shortText)}</span>
            </div>
          </div>
          <span class="event-repeat">${escapeHtml(repeatLabel)}</span>
        </div>

        <div class="event-progress-wrap">
          <div class="event-progress-bar">
            <div class="event-progress-fill" style="width:${progressPct}%"></div>
          </div>
          <span class="event-progress-label">${
            cd.totalDays <= 0 ? "Today!" :
            cd.totalDays === 0 ? "Today!" :
            `${cd.totalDays}d`
          }</span>
        </div>

        <div class="event-dates">
          <span>📅 ${escapeHtml(event.date_iso || "—")}${
            event.all_day === false && event.time_hm
              ? ` · ${escapeHtml(event.time_hm)}`
              : ""
          }</span>
          <span class="event-dates-sep">•</span>
          <span>🗓️ ${escapeHtml(event.date_jalali || "—")}</span>
        </div>

        <div class="event-bottom">
          <span class="status-dot status-${escapeHtml(event.notify_status || "pending")}"></span>
          <span>${escapeHtml(STATUS_LABELS[event.notify_status] || "Pending")}</span>
        </div>
      `;

      art.addEventListener("click",   () => openDetail(event.id));
      art.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(event.id); }
      });

      frag.appendChild(art);
    });

    els.eventsWrap.innerHTML = "";
    els.eventsWrap.appendChild(frag);
    showStatePanel();
  }

  function getCatBadgeClass(cat) {
    const map = {
      birthday: "badge-cat-birthday", work:    "badge-cat-work",
      family:   "badge-cat-family",   health:  "badge-cat-health",
      travel:   "badge-cat-travel",   finance: "badge-cat-finance",
      study:    "badge-cat-study",
    };
    return map[cat] || "";
  }

  /* ── Sheet Management ────────────────────────────────── */
  function openSheet(name, focusTgt = null) {
    state.lastFocusedElement = document.activeElement;
    if (els.sheetOverlay) els.sheetOverlay.hidden = false;

    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      const active = sheet.id === name;
      sheet.hidden = !active;
      sheet.setAttribute("aria-hidden", String(!active));
    });

    state.activeSheet = name;
    if (els.openComposerBtn) {
      els.openComposerBtn.setAttribute("aria-expanded", String(name === "composerSheet"));
    }
    updateTgBackButton();
    setTimeout(() => focusTgt?.focus?.(), 40);
  }

  function closeSheets() {
    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      sheet.hidden = true;
      sheet.setAttribute("aria-hidden", "true");
    });
    if (els.sheetOverlay) els.sheetOverlay.hidden = true;
    state.activeSheet = null;
    if (els.openComposerBtn) els.openComposerBtn.setAttribute("aria-expanded", "false");
    updateTgBackButton();
    state.lastFocusedElement?.focus?.();
  }

  function updateTgBackButton() {
    if (!tg?.BackButton) return;
    try {
      tg.BackButton.hide();
      tg.BackButton.offClick(handleTgBack);
      if (state.activeSheet) {
        tg.BackButton.onClick(handleTgBack);
        tg.BackButton.show();
      }
    } catch (_) {}
  }

  function handleTgBack() { if (state.activeSheet) closeSheets(); }

  /* ── Composer ────────────────────────────────────────── */
  // Kept in sync with the <option> values in index.html. Anything outside this
  // list (an event saved by a future build, say) falls back to one hour.
  const REMINDER_OFFSETS = [0, 15, 30, 60, 120, 1440, 10080];
  const DEFAULT_OFFSET_MINUTES = 60;

  // An all-day event has no start time, so "15 minutes before" means nothing:
  // it takes a wall-clock reminder instead. A timed event takes an offset.
  function updateAllDayVisibility() {
    const allDay = els.allDay ? els.allDay.checked : true;
    if (els.eventTimeWrap)      els.eventTimeWrap.hidden      = allDay;
    if (els.reminderTimeWrap)   els.reminderTimeWrap.hidden   = !allDay;
    if (els.reminderOffsetWrap) els.reminderOffsetWrap.hidden = allDay;
  }

  function updateRepeatUntilVisibility() {
    if (!els.repeatUntilWrap) return;
    const isRecurring = !!(els.repeat?.value && els.repeat.value !== "none");
    els.repeatUntilWrap.hidden = !isRecurring;
    if (!isRecurring && els.repeatUntil) els.repeatUntil.value = "";
    // Can't pick an end date before the event's own start date.
    if (els.repeatUntil && els.date?.value) els.repeatUntil.min = els.date.value;
  }

  function resetComposer() {
    els.eventForm?.reset();
    if (els.eventId)        els.eventId.value       = "";
    if (els.dateJalali)     els.dateJalali.value     = "";
    if (els.noteCharCount)  els.noteCharCount.textContent = "0 / 2000";
    state.editingEventId = null;
    if (els.composerTitle)    els.composerTitle.textContent    = "New Event";
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Set title, date and repeat pattern.";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.saveEventBtn)     els.saveEventBtn.innerHTML       = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      Save Event`;
  }

  function openCreateComposer() {
    resetComposer();
    openSheet("composerSheet", els.title);
  }

  function openEditComposer(event) {
    state.editingEventId = event.id;
    if (els.eventId)    els.eventId.value    = event.id;
    if (els.title)      els.title.value      = event.title      || "";
    if (els.date)       els.date.value       = event.date_iso   || "";
    if (els.dateJalali) els.dateJalali.value = event.date_jalali|| "";
    if (els.repeat)     els.repeat.value     = event.repeat     || "none";
    if (els.category)   els.category.value   = event.category   || "general";
    const allDay = event.all_day !== false;
    if (els.allDay)    els.allDay.checked = allDay;
    if (els.eventTime) els.eventTime.value = event.time_hm || "09:00";

    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (els.reminderTime) {
      const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9)).padStart(2, "0");
      const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0)).padStart(2, "0");
      els.reminderTime.value = `${h}:${m}`;
    }
    if (els.reminderOffset) {
      const offset = spec?.mode === "relative" ? Number(spec.offset_minutes) : DEFAULT_OFFSET_MINUTES;
      els.reminderOffset.value = String(
        REMINDER_OFFSETS.includes(offset) ? offset : DEFAULT_OFFSET_MINUTES
      );
    }
    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.pin)        els.pin.checked      = !!event.pinned;
    if (els.note)       els.note.value       = event.note       || "";
    if (els.noteCharCount) {
      els.noteCharCount.textContent = `${(event.note || "").length} / 2000`;
    }
    if (els.composerTitle)    els.composerTitle.textContent    = "Edit Event";
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Update the event details.";
    if (els.saveEventBtn)     els.saveEventBtn.textContent     = "Save Changes";
    openSheet("composerSheet", els.title);
  }

  /* ── Detail Panel ────────────────────────────────────── */
  function getEventById(id) {
    return state.events.find((e) => e.id === id) ?? null;
  }

  function openDetail(eventId) {
    const ev = getEventById(eventId);
    if (!ev) return;

    state.detailEventId = eventId;
    const cd = getCountdownData(ev.date_iso);

    // Basic fields
    if (els.detailEventTitle)    els.detailEventTitle.textContent    = ev.title || "—";
    if (els.detailCategoryBadge) {
      els.detailCategoryBadge.textContent = CATEGORY_LABELS[ev.category] || "General";
      els.detailCategoryBadge.className = `badge ${getCatBadgeClass(ev.category)}`;
    }
    if (els.detailRepeatBadge)  els.detailRepeatBadge.textContent  = REPEAT_LABELS[ev.repeat]  || "One time";
    if (els.detailPinnedBadge)  els.detailPinnedBadge.hidden        = !ev.pinned;
    if (els.detailDateIso) {
      els.detailDateIso.textContent =
        (ev.date_iso || "—") +
        (ev.all_day === false && ev.time_hm ? `  ·  ${ev.time_hm}` : "");
    }
    if (els.detailDateJalali)   els.detailDateJalali.textContent    = ev.date_jalali || "—";
    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
    if (els.detailStatus)       els.detailStatus.textContent        = STATUS_LABELS[ev.notify_status] || "—";
    if (els.detailNote)         els.detailNote.value                = ev.note        || "";

    // Pin button label
    if (els.detailPinBtn) {
      els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
    }

    // Countdown ring
    if (els.countdownDays) {
      els.countdownDays.textContent = cd.totalDays <= 0 ? "🎉" : String(Math.abs(cd.totalDays));
    }
    if (els.countdownRing) {
      els.countdownRing.className = `countdown-ring${
        cd.tone === "past"  ? " is-past"  :
        cd.tone === "today" ? " is-today" : ""
      }`;
    }
    if (els.detailCountdownText) {
      els.detailCountdownText.textContent = cd.fullText;
    }

    openSheet("detailSheet", els.detailNote);
  }

  /* ── Form Submit (Add / Edit) ────────────────────────── */
  async function submitEventForm(e) {
    e.preventDefault();

    const allDay = els.allDay ? els.allDay.checked : true;
    const eventTime = (els.eventTime?.value || "").trim();
    const [timeH, timeM] = (els.reminderTime?.value || "09:00").split(":");

    const reminders = allDay
      ? [{ mode: "absolute", hour: Number(timeH ?? 9), minute: Number(timeM ?? 0) }]
      : [{ mode: "relative", offset_minutes: Number(els.reminderOffset?.value ?? DEFAULT_OFFSET_MINUTES) }];

    const payload = {
      title:    els.title?.value.trim()    || "",
      date:     els.date?.value            || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      repeat:   els.repeat?.value          || "none",
      category: els.category?.value        || "general",
      note:     els.note?.value.trim()     || "",
      pinned:   !!els.pin?.checked,
      all_day:  allDay,
      time_hm:  allDay ? null : eventTime,
      reminders,
      // Still sent so an older server build keeps scheduling correctly.
      reminder_hour: Number(timeH ?? 9),
      reminder_minute: Number(timeM ?? 0),
      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
    };

    if (!payload.title) {
      showToast("Please enter an event title.", "error");
      els.title?.focus();
      return;
    }
    if (!payload.date) {
      showToast("Please select a date.", "error");
      els.date?.focus();
      return;
    }
    if (!allDay && !eventTime) {
      showToast("Please set the event time, or mark it as an all-day event.", "error");
      els.eventTime?.focus();
      return;
    }

    setLoading(true);
    try {
      if (state.editingEventId) {
        // ✅ FIX: event_id (was: eventid)
        await apiPost("/api/edit", { event_id: state.editingEventId, ...payload });
        showToast("Event updated successfully.", "success");
      } else {
        await apiPost("/api/add", payload);
        showToast("Event saved! You'll receive a reminder in Telegram.", "success");
      }
      closeSheets();
      resetComposer();
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Delete ──────────────────────────────────────────── */
  async function deleteCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    // ✅ FIX: custom confirm dialog (window.confirm broken in Telegram WebView)
    const ok = await showConfirm({
      title:   "Delete Event?",
      text:    `"${ev.title}" will be permanently removed.`,
      okLabel: "Delete",
      icon:    "🗑️",
    });
    if (!ok) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      await apiPost("/api/delete", { event_id: ev.id });
      closeSheets();
      showToast("Event deleted.", "success");
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Save Note ───────────────────────────────────────── */
  async function saveCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/note", {
        event_id: ev.id,
        note: els.detailNote?.value.trim() || "",
      });
      const target = getEventById(ev.id);
      if (target) target.note = data.note || "";
      showToast("Note saved.", "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  function resetCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev || !els.detailNote) return;
    els.detailNote.value = ev.note || "";
  }

  /* ── Pin ─────────────────────────────────────────────── */
  async function toggleCurrentPin() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const nextPinned = !ev.pinned;
    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/pin", { event_id: ev.id, pinned: nextPinned });
      ev.pinned = !!data.pinned;
      if (els.detailPinBtn)   els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
      if (els.detailPinnedBadge) els.detailPinnedBadge.hidden = !ev.pinned;
      await loadEvents();
      showToast(ev.pinned ? "Event pinned to top." : "Event unpinned.", "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Share ───────────────────────────────────────────── */
  async function shareCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const text = [
      `📅 ${ev.title}`,
      `📆 Gregorian: ${ev.date_iso}`,
      ev.all_day === false && ev.time_hm ? `🕒 Time: ${ev.time_hm}` : "",
      `🗓️ Jalali: ${ev.date_jalali}`,
      `🔄 Repeat: ${REPEAT_LABELS[ev.repeat] || "One time"}`,
      `🏷️ Category: ${CATEGORY_PLAIN[ev.category] || "General"}`,
      ev.note ? `📝 Note: ${ev.note}` : "",
    ].filter(Boolean).join("\n");

    try {
      if (navigator.share) {
        await navigator.share({ title: ev.title, text });
        showToast("Shared!", "success");
        return;
      }
      await copyToClipboard(text);
      showToast("Event details copied to clipboard.", "success");
    } catch (_) {
      showToast("Could not share. Please try copying manually.", "error");
    }
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:absolute;left:-9999px;top:0";
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }

  /* ── Jalali / Gregorian Sync ─────────────────────────── */
  function format2(n) { return String(n).padStart(2, "0"); }

  function jalaliToGregorian(jy, jm, jd) {
    let gy = jy > 979 ? (gy = 1600, jy -= 979, 1600) : (jy -= 0, 621);
    if (jy > 979) { gy = 1600; jy -= 979; } else { gy = 621; }
    let days = 365*jy + Math.floor(jy/33)*8 + Math.floor(((jy%33)+3)/4) + 78 + jd +
      (jm < 7 ? (jm-1)*31 : (jm-7)*30 + 186);
    gy += 400*Math.floor(days/146097); days %= 146097;
    if (days > 36524) { gy += 100*Math.floor(--days/36524); days %= 36524; if (days >= 365) days++; }
    gy += 4*Math.floor(days/1461); days %= 1461;
    if (days > 365) { gy += Math.floor((days-1)/365); days = (days-1)%365; }
    let gd = days + 1;
    const sal = [0,31,((gy%4===0&&gy%100!==0)||gy%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
    let gm = 0;
    for (gm = 1; gm <= 12; gm++) { if (gd <= sal[gm]) break; gd -= sal[gm]; }
    return { gy, gm, gd };
  }

  function gregorianToJalali(gy, gm, gd) {
    const g_d_m = [0,31,59,90,120,151,181,212,243,273,304,334];
    let jy = gy > 1600 ? (gy -= 1600, 979) : (gy -= 621, 0);
    const gy2 = gm > 2 ? gy+1 : gy;
    let days = 365*gy + Math.floor((gy2+3)/4) - Math.floor((gy2+99)/100) +
      Math.floor((gy2+399)/400) - 80 + gd + g_d_m[gm-1];
    jy += 33*Math.floor(days/12053); days %= 12053;
    jy += 4*Math.floor(days/1461); days %= 1461;
    if (days > 365) { jy += Math.floor((days-1)/365); days = (days-1)%365; }
    const jm = days < 186 ? 1+Math.floor(days/31) : 7+Math.floor((days-186)/30);
    const jd = 1 + (days < 186 ? days%31 : (days-186)%30);
    return { jy, jm, jd };
  }

  function syncJalaliFromGregorian() {
    const val = els.date?.value;
    if (!val) { if (els.dateJalali) els.dateJalali.value = ""; return; }
    const [gy, gm, gd] = val.split("-").map(Number);
    if (!gy || !gm || !gd) return;
    const j = gregorianToJalali(gy, gm, gd);
    if (els.dateJalali) els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function syncGregorianFromJalali() {
    const raw = (els.dateJalali?.value || "").trim().replace(/-/g, "/");
    if (!raw) return;
    const parts = raw.split("/");
    if (parts.length !== 3) return;
    const [jy, jm, jd] = parts.map(Number);
    if (!jy || !jm || !jd) return;
    const g = jalaliToGregorian(jy, jm, jd);
    if (els.date) els.date.value = `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
  }

  /* ── Escape HTML ─────────────────────────────────────── */
  function escapeHtml(v) {
    return String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /* ── Event Bindings ──────────────────────────────────── */
  function bindEvents() {
    // Header / nav
    els.refreshBtn?.addEventListener("click", () => loadEvents());
    els.retryBtn?.addEventListener("click",   () => loadEvents());
    els.emptyAddBtn?.addEventListener("click", openCreateComposer);
    els.openComposerBtn?.addEventListener("click", openCreateComposer);
    els.closeComposerX?.addEventListener("click", closeSheets);
    els.closeDetailX?.addEventListener("click",   closeSheets);
    els.cancelBtn?.addEventListener("click",      closeSheets);
    els.sheetOverlay?.addEventListener("click",   closeSheets);

    // Form
    els.eventForm?.addEventListener("submit", submitEventForm);
    els.date?.addEventListener("change", syncJalaliFromGregorian);
    els.date?.addEventListener("change", updateRepeatUntilVisibility);
    els.repeat?.addEventListener("change", updateRepeatUntilVisibility);
    els.allDay?.addEventListener("change", updateAllDayVisibility);
    els.dateJalali?.addEventListener("change", syncGregorianFromJalali);
    els.dateJalali?.addEventListener("blur",   syncGregorianFromJalali);

    // Note char counter
    els.note?.addEventListener("input", () => {
      const len = els.note.value.length;
      if (els.noteCharCount) els.noteCharCount.textContent = `${len} / 2000`;
    });

    // Search
    els.searchInput?.addEventListener("input", (e) => {
      state.searchTerm = e.target.value || "";
      applyFilters();
      renderEvents();
    });

    // Filters
    els.filterButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        state.currentFilter = btn.dataset.filter || "all";
        els.filterButtons.forEach((b) => b.classList.toggle("is-active", b === btn));
        applyFilters();
        renderEvents();
      });
    });

    // Detail actions
    els.detailEditBtn?.addEventListener("click",        () => openEditComposer(getEventById(state.detailEventId)));
    els.detailDeleteBtn?.addEventListener("click",      deleteCurrentEvent);
    els.detailPinBtn?.addEventListener("click",         toggleCurrentPin);
    els.detailShareBtn?.addEventListener("click",       shareCurrentEvent);
    els.detailNoteSaveBtn?.addEventListener("click",    saveCurrentNote);
    els.detailNoteCancelBtn?.addEventListener("click",  resetCurrentNote);

    // Load more
    els.loadMoreBtn?.addEventListener("click", () => loadEvents(true));

    // Onboarding
    els.onboardingSkipBtn?.addEventListener("click", completeOnboarding);
    els.onboardingNextBtn?.addEventListener("click", advanceOnboarding);

    // Keyboard
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (els.confirmOverlay && !els.confirmOverlay.hidden) {
          els.confirmOverlay.hidden = true;
          return;
        }
        if (state.activeSheet) closeSheets();
      }
    });
  }

  /* ── Onboarding (first run only) ────────────────────── */
  const ONBOARDING_KEY = "tmp_onboarding_seen_v1";
  const ONBOARDING_STEPS = [
    {
      icon: "🗓️",
      title: "Never miss what matters",
      text: "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.",
    },
    {
      icon: "🔔",
      title: "Reminders come straight to Telegram",
      text: "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.",
    },
    {
      icon: "🌗",
      title: "Gregorian & Jalali, together",
      text: "Every date shows in both calendars automatically. Tap the + button below to add your first event.",
    },
  ];
  let onboardingStep = 0;

  function showOnboardingIfNeeded() {
    if (!els.onboardingOverlay) return;
    try {
      if (localStorage.getItem(ONBOARDING_KEY)) return;
    } catch (_) {
      return; // storage blocked (e.g. private mode) — don't force this on every load
    }
    onboardingStep = 0;
    renderOnboardingStep();
    els.onboardingOverlay.hidden = false;
    els.onboardingOverlay.setAttribute("aria-hidden", "false");
  }

  function renderOnboardingStep() {
    const step = ONBOARDING_STEPS[onboardingStep];
    if (els.onboardingIcon)  els.onboardingIcon.textContent  = step.icon;
    if (els.onboardingTitle) els.onboardingTitle.textContent = step.title;
    if (els.onboardingText)  els.onboardingText.textContent  = step.text;
    if (els.onboardingNextBtn) {
      els.onboardingNextBtn.textContent =
        onboardingStep === ONBOARDING_STEPS.length - 1 ? "Get Started" : "Next";
    }
    if (els.onboardingDots) {
      [...els.onboardingDots.children].forEach((dot, i) => {
        dot.classList.toggle("is-active", i === onboardingStep);
      });
    }
  }

  function advanceOnboarding() {
    try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    if (onboardingStep < ONBOARDING_STEPS.length - 1) {
      onboardingStep += 1;
      renderOnboardingStep();
    } else {
      completeOnboarding();
    }
  }

  function completeOnboarding() {
    if (els.onboardingOverlay) {
      els.onboardingOverlay.hidden = true;
      els.onboardingOverlay.setAttribute("aria-hidden", "true");
    }
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) {}
  }

  /* ── Boot ────────────────────────────────────────────── */
  initTelegram();
  bindEvents();
  loadEvents();
  showOnboardingIfNeeded();
})();
'''

CONTENT_5 = r'''from __future__ import annotations

from datetime import timedelta

import pytest

from app.schemas.responses import EventOut


@pytest.mark.anyio
async def test_health_endpoint(async_client) -> None:
    from app.routes import health as health_module

    async def fake_ping_database() -> bool:
        return True

    original = health_module.ping_database
    health_module.ping_database = fake_ping_database
    try:
        response = await async_client.get("/health")
    finally:
        health_module.ping_database = original

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert "ts" in data


@pytest.mark.anyio
async def test_api_list_returns_targets(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_list_events_for_user(user_id: str, payload):
        return (
            [
                EventOut(
                    id="evt1",
                    title="Birthday",
                    date_iso="2026-04-20",
                    date_jalali="1405/01/31",
                    repeat="yearly",
                    notify_status="pending",
                    tz_name="UTC",
                    category="birthday",
                    pinned=True,
                    note="Cake",
                )
            ],
            False,
        )

    original_auth = events_module.get_authenticated_user_id
    original_list = events_module.list_events_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.list_events_for_user = fake_list_events_for_user

    try:
        response = await async_client.post(
            "/api/list",
            json={"initData": "dummy", "skip": 0},
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.list_events_for_user = original_list

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["targets"]) == 1
    assert data["targets"][0]["title"] == "Birthday"
    assert data["meta"]["returned"] == 1
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_api_add_success(async_client) -> None:
    from app.routes import events as events_module

    captured: dict = {}

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_add_event_for_user(user_id: str, payload) -> None:
        captured["user_id"] = user_id
        captured["title"] = payload.title
        captured["date"] = payload.date

    original_auth = events_module.get_authenticated_user_id
    original_add = events_module.add_event_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.add_event_for_user = fake_add_event_for_user

    try:
        response = await async_client.post(
            "/api/add",
            json={
                "initData": "dummy",
                "title": "Doctor Visit",
                "date": "2026-05-01",
                "timezone": "UTC",
                "repeat": "none",
                "category": "health",
                "note": "",
                "pinned": False,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.add_event_for_user = original_add

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert captured["user_id"] == "123"
    assert captured["title"] == "Doctor Visit"
    assert captured["date"] == "2026-05-01"


@pytest.mark.anyio
async def test_api_add_rejects_invalid_payload(async_client) -> None:
    response = await async_client.post(
        "/api/add",
        json={
            "initData": "dummy",
            "title": "",
            "date": "2026/05/01",
            "timezone": "UTC",
            "repeat": "none",
            "category": "health",
            "note": "",
            "pinned": False,
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_api_note_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_save_note_for_user(user_id: str, payload) -> str:
        assert user_id == "123"
        assert payload.event_id == "event123"
        return payload.note

    original_auth = events_module.get_authenticated_user_id
    original_save = events_module.save_note_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.save_note_for_user = fake_save_note_for_user

    try:
        response = await async_client.post(
            "/api/note",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "note": "Buy candles",
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.save_note_for_user = original_save

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["note"] == "Buy candles"


@pytest.mark.anyio
async def test_api_pin_success(async_client) -> None:
    from app.routes import events as events_module

    async def fake_get_authenticated_user_id(request, init_data: str) -> str:
        return "123"

    async def fake_set_pin_for_user(user_id: str, payload) -> bool:
        assert user_id == "123"
        return payload.pinned

    original_auth = events_module.get_authenticated_user_id
    original_pin = events_module.set_pin_for_user
    events_module.get_authenticated_user_id = fake_get_authenticated_user_id
    events_module.set_pin_for_user = fake_set_pin_for_user

    try:
        response = await async_client.post(
            "/api/pin",
            json={
                "initData": "dummy",
                "event_id": "event123",
                "pinned": True,
            },
        )
    finally:
        events_module.get_authenticated_user_id = original_auth
        events_module.set_pin_for_user = original_pin

    assert response.status_code == 200
    assert response.json() == {"success": True, "pinned": True}


@pytest.mark.anyio
async def test_root_redirects_to_webapp(async_client) -> None:
    response = await async_client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/webapp"


def _event_payload(**overrides):
    from app.schemas.requests import AddEventRequest

    base = {
        "initData": "dummy",
        "title": "Standup",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "none",
    }
    base.update(overrides)
    return AddEventRequest(**base)


def test_normalize_event_input_defaults_to_an_all_day_event() -> None:
    """A client that has not been updated sends neither all_day nor reminders;
    it must keep producing exactly the old schedule."""
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(_event_payload(reminder_hour=7, reminder_minute=30))

    assert doc["all_day"] is True
    assert doc["time_hm"] is None
    assert doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]
    assert doc["reminder_hour"] == 7
    assert doc["notify_status"] == "pending"


def test_normalize_event_input_schedules_a_relative_reminder() -> None:
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(
            all_day=False,
            time_hm="14:30",
            reminders=[{"mode": "relative", "offset_minutes": 60}],
        )
    )

    assert doc["all_day"] is False
    assert doc["time_hm"] == "14:30"
    assert doc["event_ts_utc"] - doc["next_notify_at"] == timedelta(minutes=60)


def test_normalize_event_input_requires_a_time_when_not_all_day() -> None:
    from fastapi import HTTPException

    from app.services.events import _normalize_event_input

    with pytest.raises(HTTPException) as excinfo:
        _normalize_event_input(_event_payload(all_day=False))

    assert excinfo.value.detail == "TIME_REQUIRED"


def test_normalize_event_input_marks_a_finished_series_done() -> None:
    from app.services.events import _normalize_event_input

    doc = _normalize_event_input(
        _event_payload(date="2020-01-01", repeat="daily", repeat_until="2020-01-02")
    )

    assert doc["next_notify_at"] is None
    assert doc["notify_status"] == "done"


def test_add_request_accepts_the_exact_payload_the_mini_app_sends() -> None:
    """The request models forbid unknown keys, so a field added in app.js and
    forgotten here would 422 in production while every unit test still passed.
    These two dicts are the payloads submitEventForm builds, key for key."""
    from app.schemas.requests import AddEventRequest
    from app.services.events import _normalize_event_input

    all_day_payload = {
        "initData": "dummy",
        "title": "Mom birthday",
        "date": "2099-04-20",
        "timezone": "Europe/Berlin",
        "repeat": "yearly",
        "category": "birthday",
        "note": "",
        "pinned": False,
        "all_day": True,
        "time_hm": None,
        "reminders": [{"mode": "absolute", "hour": 7, "minute": 30}],
        "reminder_hour": 7,
        "reminder_minute": 30,
        "repeat_until": None,
    }
    timed_payload = {
        **all_day_payload,
        "title": "Team standup",
        "repeat": "daily",
        "category": "work",
        "all_day": False,
        "time_hm": "14:30",
        "reminders": [{"mode": "relative", "offset_minutes": 60}],
    }

    all_day_doc = _normalize_event_input(AddEventRequest(**all_day_payload))
    assert all_day_doc["time_hm"] is None
    assert all_day_doc["reminders"] == [{"mode": "absolute", "hour": 7, "minute": 30}]

    timed_doc = _normalize_event_input(AddEventRequest(**timed_payload))
    assert timed_doc["time_hm"] == "14:30"
    assert timed_doc["event_ts_utc"] - timed_doc["next_notify_at"] == timedelta(minutes=60)
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "app/services/auth.py",
        "fcf182d4226ccebdca1281d0b482ff4d24b9f6278a541ede4910d388a141cb18",
        "b6c0b58a8d983369247b2511a24247e509052cf8d39a8dc8044aec046c491a61",
        CONTENT_1,
    ),
    (
        "tests/test_auth.py",
        "b780a2aaca97269edc02e028ab90dce2f877a50c2a76a69be038276341d9905c",
        "bb3c008d34e190c7ac4ba13836e369a07a12a4469403ae59b03701ede7b45c4d",
        CONTENT_2,
    ),
    (
        "templates/index.html",
        "dc68b08bc82046432ea6ef4a9eb6de68777af6514bb828f017aae5254a17c22e",
        "5ff465d5a07357581361dfeea2af2a40eafe0aaf5db92bb3cdf210dfa498c18f",
        CONTENT_3,
    ),
    (
        "static/app.js",
        "bd295cc2fc850059ecd1d1479c4775c8341a0c09fe662353bfa426c4b9dc642d",
        "0dfad908d6c40651fdf106b6427a8e0f6f3b8185f39d5f16f95518a56391c1a4",
        CONTENT_4,
    ),
    (
        "tests/test_events_api.py",
        "8b188a0d325dd457f6a4cc3a8dde3bd3f45b69770730444d90606f2250e8f446",
        "5cef103be7bb307f687bc938704e90666f2a4a8639a6656e2864988c78cff6b6",
        CONTENT_5,
    ),
]


def apply_files() -> None:
    for rel, expected_sha, new_sha, content in FILES:
        path = ROOT / rel
        if not path.exists():
            log("MISSING", f"{rel} — not found, skipped")
            continue

        digest = sha(path.read_bytes())

        if digest == new_sha:
            log("skipped", f"{rel} (already applied)")
            continue

        if digest != expected_sha:
            log("MANUAL", f"{rel} — not the version this script expects, left untouched")
            continue

        path.write_bytes(content.encode("utf-8"))
        log("updated", rel)


def ignore_helper_scripts() -> None:
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        log("MISSING", ".gitignore")
        return

    text = gitignore.read_text(encoding="utf-8")
    if "fix_batch*.py" in text:
        log("skipped", ".gitignore (already ignores fix_batch*.py)")
        return

    gitignore.write_text(
        text.rstrip("\n")
        + "\n\n# One-off migration scripts — run them, do not commit them\nfix_batch*.py\n",
        encoding="utf-8",
    )
    log("extended", ".gitignore")


def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    report.append("\nApplying auth fix and batch 3b")
    apply_files()

    report.append("\nHousekeeping")
    ignore_helper_scripts()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  python -m ruff check . ; python -m pytest -q      # expect 65 passing\n"
        "  git add -A\n"
        '  git commit -m "Accept both initData check strings; add event times to the composer"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Some files were left untouched — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
