"""The client's address behind Render's proxies (Batch 27, ADR 0016).

Measured in production with a forged header: X-Forwarded-For arrived as
    <what the client wrote>,<client, added by Cloudflare>, <Cloudflare edge,
    added by Render>, <Render internal>
and both client_ip and uvicorn took the leftmost entry, the forged one, so a
new invented address each time escaped every per-address limit. The client is
the rightmost entry that is neither internal nor a Cloudflare edge; whatever a
client writes sits to its left and is never reached. Documentation addresses
(RFC 5737, RFC 3849) stand in for real ones.
"""
from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from tests.harness import install_fake_db, teardown_fake_db

EDGE, INTERNAL, CLIENT = "172.69.109.16", "10.31.28.2", "198.51.100.23"
BAD = "auth_date=1700000000&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64


def client_address(forwarded, peer="10.0.0.9"):
    from app.utils.net import client_address as find

    return find(forwarded, peer)


@pytest.mark.parametrize(("chain", "expected"), [
    (f"203.0.113.7,{CLIENT}, {EDGE}, {INTERNAL}", CLIENT),                 # as measured
    (f"192.0.2.1, 203.0.113.99, {CLIENT}, {EDGE}, {INTERNAL}", CLIENT),    # several forged
    (f"172.69.1.1, {CLIENT}, {EDGE}, {INTERNAL}", CLIENT),                 # a forged edge
    (f"{CLIENT}, {INTERNAL}", CLIENT),                                     # no Cloudflare
    (f"not-an-address, {CLIENT}, {INTERNAL}", CLIENT),                     # garbage skipped
    ("2001:db8::7, 2606:4700::1, 10.31.28.2", "2001:db8::7"),              # IPv6
])
def test_the_client_is_the_rightmost_address_that_is_not_a_proxy(chain, expected):
    assert client_address(chain) == expected


def test_without_a_chain_the_peer_is_the_client():
    assert client_address(None, "203.0.113.50") == "203.0.113.50"
    assert client_address(f"{EDGE}, {INTERNAL}", "203.0.113.50") == "203.0.113.50"


@pytest.fixture
def clean():
    from app.services import auth

    auth._rate_store.clear()
    install_fake_db()
    yield
    teardown_fake_db()
    auth._rate_store.clear()


async def post_forged(i: int):
    chain = {"X-Forwarded-For": f"203.0.113.{i},{CLIENT}, {EDGE}, {INTERNAL}"}
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.post("/api/list", json={"initData": BAD}, headers=chain)


async def test_a_new_forged_address_each_time_no_longer_escapes_the_limit(clean):
    codes = [(await post_forged(i)).status_code for i in range(31)]
    assert codes[:30] == [403] * 30 and codes[30] == 429, codes


async def test_the_failure_log_names_the_real_client(clean, caplog):
    caplog.set_level(logging.WARNING)
    await post_forged(7)
    line = next(r.getMessage() for r in caplog.records if "Bad Telegram initData" in r.getMessage())
    assert f"ip={CLIENT} " in line, line
