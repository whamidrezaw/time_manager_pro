"""The two calls to Telegram's HTTP API (Batch 29, review R1 and R6).

get_chat_member_status guards group links: a restricted user is in the chat
only while is_member says so, and one who left while restricted keeps that
status (Bot API, ChatMemberRestricted). A refusal or an outage must count as
not a member. save_prepared_inline_message had no test at all.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import app.services.telegram_api as api


class FakeResponse:
    def __init__(self, body, not_json=False):
        self.body, self.not_json, self.status_code = body, not_json, 200

    def json(self):
        if self.not_json:
            raise ValueError("not JSON")
        return self.body


def fake_http(monkeypatch, body=None, error=None, not_json=False) -> dict:
    sent: dict = {}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            sent.update(url=url, json=json)
            if error:
                raise error
            return FakeResponse(body, not_json)

    monkeypatch.setattr(api, "httpx", SimpleNamespace(AsyncClient=Client))
    return sent


@pytest.mark.parametrize(("result", "expected"), [
    ({"status": "member"}, "member"),
    ({"status": "creator"}, "creator"),
    ({"status": "left"}, "left"),
    ({"status": "kicked"}, "kicked"),
    ({"status": "restricted", "is_member": True}, "restricted"),
    ({"status": "restricted", "is_member": False}, "left"),  # left while restricted
])
async def test_membership_is_read_from_telegram(monkeypatch, result, expected):
    fake_http(monkeypatch, body={"ok": True, "result": result})
    assert await api.get_chat_member_status(-100555, "111222333") == expected


@pytest.mark.parametrize("failure", [{"body": {"ok": False, "description": "chat not found"}},
                                     {"error": httpx.ConnectError("down")}])
async def test_a_refusal_or_an_outage_is_not_membership(monkeypatch, failure):
    fake_http(monkeypatch, **failure)
    assert await api.get_chat_member_status(-100555, "111222333") == ""


async def test_a_prepared_message_returns_its_id_and_never_goes_to_bots(monkeypatch):
    sent = fake_http(monkeypatch, body={"ok": True, "result": {"id": "prepared-1"}})
    assert await api.save_prepared_inline_message("111222333", {"type": "photo"}) == "prepared-1"
    assert sent["json"]["user_id"] == 111222333 and sent["json"]["allow_bot_chats"] is False


@pytest.mark.parametrize("failure", [{"body": {"ok": False, "description": "bad"}}, {"not_json": True}])
async def test_a_refused_or_garbled_answer_raises_prepare_failed(monkeypatch, failure):
    fake_http(monkeypatch, **failure)
    with pytest.raises(RuntimeError, match="PREPARE_FAILED"):
        await api.save_prepared_inline_message("111222333", {"type": "photo"})
