"""
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


# Reads and writes are counted separately: a search box issues a request per
# query, and sharing one budget would let typing lock the user out of saving.
READ_SCOPE = "read"
WRITE_SCOPE = "write"
# Anonymous requests to the public share pages, keyed by client IP rather than
# by a Telegram user id. Its own scope so a flood of strangers can never spend
# a signed-in user's budget.
PUBLIC_SCOPE = "public"

# The fallback store keeps one key per identity. With user ids that grows with
# the user base; with IP keys it grows with the internet, so it needs a ceiling.
MAX_RATE_KEYS = 10_000


def scope_limit(scope: str, settings: Settings) -> int:
    if scope == READ_SCOPE:
        return settings.rate_limit_read_count
    if scope == PUBLIC_SCOPE:
        return settings.rate_limit_public_count
    return settings.rate_limit_count


def _prune_rate_history(key: str, window_seconds: int = 60) -> list[float]:
    now = time.time()
    history = [t for t in _rate_store.get(key, []) if now - t < window_seconds]
    if history:
        _rate_store[key] = history
    else:
        # An empty list was still a permanent entry. Harmless for a few
        # thousand user ids, a slow leak once the key is a client IP.
        _rate_store.pop(key, None)
    return history


def _evict_rate_keys() -> None:
    """Keep the fallback store bounded.

    Only reached when MongoDB is unavailable, which is exactly when the
    process should not also be running out of memory. Oldest activity goes
    first; the worst case is that a handful of callers get a fresh budget.
    """
    if len(_rate_store) <= MAX_RATE_KEYS:
        return
    ordered = sorted(_rate_store.items(), key=lambda kv: max(kv[1], default=0.0))
    for key, _ in ordered[: len(_rate_store) - MAX_RATE_KEYS]:
        _rate_store.pop(key, None)


def _check_rate_limit_memory(
    user_id: str,
    settings: Settings,
    scope: str = WRITE_SCOPE,
) -> None:
    """Fallback in-memory rate limit — only used when MongoDB is unavailable."""
    key = f"{scope}:{user_id}"
    history = _prune_rate_history(key)
    if len(history) >= scope_limit(scope, settings):
        logger.warning("Rate limit exceeded (memory fallback): user_id=%s scope=%s", user_id, scope)
        raise HTTPException(status_code=429, detail="RATE_LIMIT")
    history.append(time.time())
    _rate_store[key] = history
    _evict_rate_keys()


def check_rate_limit(
    user_id: str,
    settings: Settings | None = None,
    scope: str = WRITE_SCOPE,
) -> None:
    """Sync version — only used in tests."""
    settings = settings or get_settings()
    _check_rate_limit_memory(user_id, settings, scope)


async def check_rate_limit_mongo(
    user_id: str,
    settings: Settings,
    scope: str = WRITE_SCOPE,
) -> None:
    try:
        from app.db import get_database
        db = get_database()
        rate_coll = db["rate_limits"]

        now = datetime.now(timezone.utc)
        bucket = now.replace(second=0, microsecond=0)

        # Both counters live in the same document, keyed the same way as before.
        # A separate document per scope would need the unique index on
        # (user_id, bucket) rebuilt, and dropping a live unique index is not
        # worth it for two integers.
        field = "read_count" if scope == READ_SCOPE else "count"

        doc = await rate_coll.find_one_and_update(
            {"user_id": user_id, "bucket": bucket},
            {
                "$inc": {field: 1},
                "$setOnInsert": {"ts": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        if int(doc.get(field, 0)) > scope_limit(scope, settings):
            raise HTTPException(status_code=429, detail="RATE_LIMIT")

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limit mongo unavailable, using memory fallback: %s", exc)
        _check_rate_limit_memory(user_id, settings, scope)


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
    scope: str = WRITE_SCOPE,
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

    await check_rate_limit_mongo(user_id, settings, scope)

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
    scope: str = WRITE_SCOPE,
) -> str:
    auth_result = await validate_init_data(request, init_data, settings, scope)
    return auth_result["user_id"]


def client_ip(request) -> str:
    """Best-effort client address for anonymous rate limiting.

    Render terminates TLS and forwards the real address in X-Forwarded-For, so
    request.client.host is the proxy. The leftmost entry is the one the proxy
    saw. It is client-supplied and therefore spoofable: an attacker who rotates
    the header gets a fresh budget each time. That is a ceiling on how much
    this can do, not a reason to skip it — it stops the ordinary case of one
    host hammering a share link, and the expensive work behind these routes is
    now 55ms in a threadpool rather than 317ms on the event loop.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return getattr(getattr(request, "client", None), "host", "") or "unknown"


async def check_public_rate_limit(request, settings: Settings | None = None) -> None:
    """Rate limit for routes with no initData to identify the caller."""
    settings = settings or get_settings()
    await check_rate_limit_mongo(f"ip:{client_ip(request)}", settings, scope=PUBLIC_SCOPE)
