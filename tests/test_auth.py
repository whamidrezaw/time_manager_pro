from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.auth import (
    build_data_check_string,
    check_rate_limit,
    compute_telegram_hash,
    parse_init_data,
    parse_init_user,
    validate_auth_date,
    validate_init_data,
)
from app.config import get_settings


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


def test_build_data_check_string_excludes_hash_and_signature() -> None:
    parsed = {
        "auth_date": "111",
        "user": '{"id":1}',
        "hash": "abc",
        "signature": "sig",
        "query_id": "q1",
    }
    result = build_data_check_string(parsed)

    assert "hash=" not in result
    assert "signature=" not in result
    assert "auth_date=111" in result
    assert "query_id=q1" in result


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