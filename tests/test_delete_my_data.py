"""/deletemydata: erasure on request (Batch 27, step 3; GDPR Art. 17).

As chosen: a bot command with a confirmation button. Everything the app keeps
about the user goes: their events with their public and join links, their
user record, their username, a limit the admin set for them, their rate-limit
counters and their membership of group chats. Their invitees stop pointing at
them, and the admin's audit trail keeps what was done, not to whom.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.config import get_settings
from app.db import get_database
from tests.harness import install_fake_db, seed_event, teardown_fake_db

ROOT = Path(__file__).resolve().parents[1]
ME, OTHER = "111222333", "444555666"


@pytest.fixture
def db():
    install_fake_db()
    yield get_database()
    teardown_fake_db()


async def seed_everything(db, uid: str, username: str, chat_id: int) -> None:
    await seed_event(user_id=uid, title="doctor", public_enabled=True, public_token=f"tok{uid}",
                     share_id=f"share{uid}", share_role="owner")
    await db["users"].insert_one({"_id": uid, "referral_code": f"C{uid[:5]}"})
    await db["usernames"].insert_one({"_id": username, "user_id": uid})
    await db["limit_overrides"].insert_one({"_id": uid, "limit": 99})
    await db["rate_limits"].insert_one({"user_id": uid, "bucket": 1, "count": 3})
    await db["chats"].insert_one({"_id": chat_id, "members": [uid, "777"], "active": True, "title": "Family"})
    await db["admin_audit"].insert_one({"admin": "1", "action": "limit", "target": f"@{username.title()}"})
    await db["admin_audit"].insert_one({"admin": "1", "action": "limit", "target": uid})


def mentions(value, needles: tuple[str, ...]) -> bool:
    if isinstance(value, dict):
        return any(mentions(k, needles) or mentions(v, needles) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(mentions(v, needles) for v in value)
    return isinstance(value, str) and any(n in value.lower() for n in needles)


async def test_everything_about_the_user_is_erased_and_nobody_elses(db):
    from app.services.erasure import delete_user_data

    await seed_everything(db, ME, "hamid", -1001)
    await seed_everything(db, OTHER, "sara", -1002)
    await db["users"].update_one({"_id": OTHER}, {"$set": {"referred_by": ME}})  # I invited Sara

    counts = await delete_user_data(ME)

    left = [(name, doc) for name in await db.list_collection_names()
            async for doc in db[name].find({}) if mentions(doc, (ME, "hamid"))]
    assert not left, left
    assert counts["events"] == 1
    for name, query in [("events", {"user_id": OTHER}), ("users", {"_id": OTHER}),
                        ("usernames", {"user_id": OTHER}), ("limit_overrides", {"_id": OTHER}),
                        ("rate_limits", {"user_id": OTHER}), ("chats", {"members": OTHER}),
                        ("admin_audit", {"target": OTHER})]:
        assert await db[name].count_documents(query) == 1, name
    assert (await db["chats"].find_one({"_id": -1001}))["members"] == ["777"]  # the group stays


def test_every_collection_the_code_uses_is_known_to_the_erasure():
    from app.services.erasure import ERASED, WITHOUT_USER_DATA

    code = " ".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").rglob("*.py"))
    used = set(re.findall(r'(?:get_database\(\)|db)\["([a-z_]+)"\]', code))
    assert used == set(ERASED) | set(WITHOUT_USER_DATA), sorted(used ^ (set(ERASED) | set(WITHOUT_USER_DATA)))


class FakeBot:
    calls: list = []
    defaults = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, chat_id, text, **kwargs):
        FakeBot.calls.append(("send", chat_id, text, kwargs.get("reply_markup")))

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        FakeBot.calls.append(("edit", chat_id, text, kwargs.get("reply_markup")))

    async def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        FakeBot.calls.append(("answer", callback_query_id, text, None))


@pytest.fixture
def bot(monkeypatch, db):
    import app.routes.telegram as telegram

    FakeBot.calls = []
    monkeypatch.setattr(telegram, "Bot", FakeBot)
    return FakeBot


def person(uid: str) -> dict:
    return {"id": int(uid), "is_bot": False, "first_name": "H", "language_code": "en"}


def command(text: str, uid: str = ME, chat: dict | None = None) -> dict:
    return {"update_id": 1, "message": {
        "message_id": 5, "date": int(time.time()), "from": person(uid), "text": text,
        "chat": chat or {"id": int(uid), "type": "private"},
        "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}]}}


def press(data: str, uid: str = ME, sent: float | None = None) -> dict:
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "from": person(uid), "chat_instance": "ci", "data": data,
        "message": {"message_id": 9, "date": int(sent or time.time()), "text": "?",
                    "chat": {"id": int(ME), "type": "private"}}}}


async def webhook(payload: dict):
    headers = {"X-Telegram-Bot-Api-Secret-Token": get_settings().telegram_webhook_secret}
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.post("/telegram/webhook", json=payload, headers=headers)


def texts(kind: str) -> list[str]:
    return [call[2] or "" for call in FakeBot.calls if call[0] == kind]


async def test_the_command_asks_first_and_erases_nothing(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    assert (await webhook(command("/deletemydata"))).status_code == 200
    sent = [call for call in FakeBot.calls if call[0] == "send"]
    buttons = [b.callback_data for row in sent[-1][3].inline_keyboard for b in row]
    assert buttons == [f"delme:{ME}", "delme:no"], buttons
    assert await db["events"].count_documents({"user_id": ME}) == 1


async def test_confirming_erases_everything(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    await webhook(press(f"delme:{ME}"))
    assert await db["events"].count_documents({"user_id": ME}) == 0
    assert await db["users"].count_documents({"_id": ME}) == 0
    assert any("deleted" in text.lower() for text in texts("edit")), FakeBot.calls


async def test_cancel_keeps_everything(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    await webhook(press("delme:no"))
    assert await db["events"].count_documents({"user_id": ME}) == 1
    assert any("nothing was deleted" in text.lower() for text in texts("edit")), FakeBot.calls


async def test_nobody_else_can_confirm_it(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    await webhook(press(f"delme:{ME}", uid=OTHER))
    assert await db["events"].count_documents({"user_id": ME}) == 1
    assert any("only the person who asked" in text.lower() for text in texts("answer")), FakeBot.calls


async def test_an_old_confirmation_has_expired(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    await webhook(press(f"delme:{ME}", sent=time.time() - 11 * 60))
    assert await db["events"].count_documents({"user_id": ME}) == 1
    assert any("expired" in text.lower() for text in texts("edit")), FakeBot.calls


async def test_in_a_group_it_asks_for_a_private_chat(bot, db):
    await seed_everything(db, ME, "hamid", -1001)
    await webhook(command("/deletemydata", chat={"id": -1001, "type": "group", "title": "Family"}))
    sent = [call for call in FakeBot.calls if call[0] == "send"]
    assert sent and sent[-1][3] is None or not getattr(sent[-1][3], "inline_keyboard", None)
    assert "private chat" in sent[-1][2].lower()
    assert await db["events"].count_documents({"user_id": ME}) == 1


def test_help_and_every_message_exist_in_both_languages():
    from app.utils.i18n import t

    for language in ("en", "fa"):
        assert "/deletemydata" in t("help", language)
        for key in ("delete_ask", "delete_yes", "delete_no", "delete_done", "delete_cancelled",
                    "delete_expired", "delete_not_yours", "delete_private_only"):
            assert t(key, language) != key, (key, language)
    assert t("delete_done", "fa") != t("delete_done", "en")
