"""Security hardening from the Stage 5 audit (Batch 27; SECURITY.md).

M1: an unauthenticated request was read and parsed whole before it could be
refused, with no size limit, no bound on initData, and no throttle on failed
authentication, while production runs one worker. L1: the failure log carried
a prefix of the correct hash and a debugging marker. L2: no
Permissions-Policy. L3: an override of the CSP left no trace.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.config import get_settings
from tests.harness import install_fake_db, teardown_fake_db

ROOT = Path(__file__).resolve().parents[1]
LIST = "/api/list"
BAD = "auth_date=1700000000&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64


@pytest.fixture(autouse=True)
def clean_state():
    from app.services import auth

    auth._rate_store.clear()
    install_fake_db()
    yield
    teardown_fake_db()
    auth._rate_store.clear()


async def post(path: str, content: bytes | None = None, json: dict | None = None,
               headers: dict | None = None):
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.post(path, content=content, json=json, headers=headers or {})


async def test_a_body_over_the_limit_is_refused_before_it_is_read():
    body = b"x" * (64 * 1024 + 1)
    response = await post(LIST, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 413, response.text


async def test_a_streamed_body_over_the_limit_is_refused_too():
    async def chunks():
        for _ in range(9):
            yield b" " * 8192  # 72 KB with no Content-Length: the header alone would not see it

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.post(LIST, content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413, response.text


async def test_an_ordinary_request_is_untouched():
    response = await post(LIST, json={"initData": BAD})
    assert response.status_code == 403, response.text


async def test_init_data_is_bounded():
    response = await post(LIST, json={"initData": "a" * 8193})
    assert response.status_code == 422, response.text


async def test_failed_authentication_is_throttled_per_address():
    one = {"X-Forwarded-For": "203.0.113.5"}
    codes = [(await post(LIST, json={"initData": BAD}, headers=one)).status_code for _ in range(31)]
    assert codes[:30] == [403] * 30 and codes[30] == 429, codes


async def test_rotating_addresses_still_meet_a_ceiling(monkeypatch):
    """The client's address can be forged, so a flood from invented addresses
    meets a ceiling on all failed authentications; a real user never fails."""
    monkeypatch.setattr(get_settings(), "rate_limit_auth_fail_global", 5, raising=False)
    codes = []
    for i in range(6):
        invented = {"X-Forwarded-For": f"198.51.100.{i}"}
        codes.append((await post(LIST, json={"initData": BAD}, headers=invented)).status_code)
    assert codes[:5] == [403] * 5 and codes[5] == 429, codes


async def test_the_failure_log_holds_no_part_of_the_correct_hash(caplog):
    from app.services.auth import compute_telegram_hash, parse_init_data

    caplog.set_level(logging.WARNING)
    await post(LIST, json={"initData": BAD}, headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    text = " ".join(r.getMessage() for r in caplog.records)
    correct = compute_telegram_hash(parse_init_data(BAD), get_settings().bot_token)
    assert "Bad Telegram initData" in text
    assert correct[:8] not in text and "TIMEPICKER" not in text and "computed" not in text, text
    assert "203.0.113.9, 10.0.0.1" in text  # the chain, to learn how Render's proxy builds it


async def test_the_permissions_policy_turns_off_what_the_app_does_not_use():
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get("/health")
    expected = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    assert response.headers.get("permissions-policy") == expected


def test_an_override_of_the_csp_is_logged(caplog):
    from app.middleware import CSP, effective_csp

    caplog.set_level(logging.WARNING)

    class Plain:
        content_security_policy = None

    class Override:
        content_security_policy = "default-src *"

    assert effective_csp(Plain()) == CSP and not caplog.records
    assert effective_csp(Override()) == "default-src *"
    assert any("CONTENT_SECURITY_POLICY" in r.getMessage() for r in caplog.records)


def test_security_md_says_what_is_defended_accepted_and_how_to_report():
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for heading in ("## Reporting a vulnerability", "## Trust boundaries", "## Accepted risks"):
        assert heading in text, heading
