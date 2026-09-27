"""Linking a group chat, and the bot joining or leaving one (Batch 29, R1).

link_member is the check that keeps a forwarded link from posting reminders
into a group the holder is not in; the webhook registers a group when the bot
is added and deactivates it when the bot is removed. None of it had a test.
"""
from __future__ import annotations

import time

import pytest

import app.services.telegram_api as api
from app.db import get_database
from tests.harness import install_fake_db, teardown_fake_db
from tests.test_delete_my_data import FakeBot, person, webhook

GROUP, ME = -100555, "111222333"
BOT = {"id": 8715777794, "is_bot": True, "first_name": "Bot"}


@pytest.fixture
def db():
    install_fake_db()
    yield get_database()
    teardown_fake_db()


async def registered():
    from app.services.chats import register_chat

    return await register_chat(GROUP, "supergroup", "Family", "1")


@pytest.mark.parametrize(("status", "linked"), [("member", True), ("restricted", True),
                                                ("left", False), ("kicked", False), ("", False)])
async def test_only_a_member_links_the_group(db, monkeypatch, status, linked):
    from app.services.chats import link_member

    record = await registered()

    async def status_of(chat_id, user_id, settings=None):
        return status

    monkeypatch.setattr(api, "get_chat_member_status", status_of)
    result = await link_member(ME, record["link_token"])
    members = (await db["chats"].find_one({"_id": GROUP}))["members"]
    assert result["success"] is linked and (ME in members) is linked, result


async def test_an_unknown_or_closed_link_links_nothing(db, monkeypatch):
    from app.services.chats import link_member

    record = await registered()

    async def member(chat_id, user_id, settings=None):
        return "member"

    monkeypatch.setattr(api, "get_chat_member_status", member)
    assert (await link_member(ME, "no-such-token"))["reason"] == "NOT_FOUND"
    await db["chats"].update_one({"_id": GROUP}, {"$set": {"active": False}})
    assert (await link_member(ME, record["link_token"]))["reason"] == "NOT_FOUND"


@pytest.fixture
def bot(monkeypatch, db):
    import app.routes.telegram as telegram

    FakeBot.calls = []
    monkeypatch.setattr(telegram, "Bot", FakeBot)
    return FakeBot


def membership(new_status: str, chat_type: str = "supergroup") -> dict:
    return {"update_id": 3, "my_chat_member": {
        "chat": {"id": GROUP, "type": chat_type, "title": "Family"}, "from": person(ME),
        "date": int(time.time()),
        "old_chat_member": {"user": BOT, "status": "left"},
        "new_chat_member": {"user": BOT, "status": new_status}}}


async def test_adding_the_bot_registers_the_group_and_tells_the_adder(bot, db):
    await webhook(membership("member"))
    chat = await db["chats"].find_one({"_id": GROUP})
    assert chat and chat["active"] and ME in chat["members"]
    assert any(call[0] == "send" and str(call[1]) == ME for call in FakeBot.calls), FakeBot.calls


async def test_removing_the_bot_deactivates_the_group(bot, db):
    await webhook(membership("member"))
    await webhook(membership("left"))
    assert (await db["chats"].find_one({"_id": GROUP}))["active"] is False
