"""Log privacy (Batch 28, ADR 0018), as chosen: no titles, and user ids as
keyed pseudonyms.

Found in production logs after Batch 27: "add_event user_id=... title='testi'
date=...". A title is whatever the user wrote, and /deletemydata cannot reach
Render's logs. A Telegram id is a number of about ten digits, so a plain hash
of it is reversed by hashing every candidate; the pseudonym is keyed instead,
and /logid gives the admin the one to search for.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import get_database
from tests.harness import install_fake_db, teardown_fake_db

ROOT = Path(__file__).resolve().parents[1]
ME = "111222333"
log = logging.getLogger("tm_pro.test")


def pseudonym(value):
    from app.observability import pseudonym as make

    return make(value)


@pytest.fixture(autouse=True)
def configured():
    from app.observability import configure_logging

    configure_logging(get_settings())


@pytest.fixture
def db():
    install_fake_db()
    yield get_database()
    teardown_fake_db()


def test_ids_in_log_arguments_become_pseudonyms(caplog):
    caplog.set_level(logging.INFO)
    log.info("user_id=%s chat=%s count=%s ms=%s", int(ME), "-1001234567890", 3, 2.5)
    text = caplog.records[-1].getMessage()
    assert ME not in text and "1001234567890" not in text, text
    assert pseudonym(ME) in text and pseudonym("-1001234567890") in text
    assert "count=3" in text and "ms=2.5" in text


def test_the_pseudonym_is_keyed_and_stable(monkeypatch):
    plain = hashlib.sha256(ME.encode()).hexdigest()
    first = pseudonym(ME)
    assert first == pseudonym(ME) and plain[:12] not in first
    monkeypatch.setattr(get_settings(), "bot_token", "999:another")
    assert pseudonym(ME) != first


async def test_adding_an_event_logs_neither_title_nor_date_nor_the_raw_id(db, caplog):
    from app.schemas.requests import AddEventRequest
    from app.services.events import add_event_for_user

    caplog.set_level(logging.INFO)
    payload = AddEventRequest(initData="x", title="Dr Kaya appointment", date="2026-12-24",
                              timezone="Europe/Berlin")
    await add_event_for_user(ME, payload, get_settings())
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Dr Kaya" not in text and "2026-12-24" not in text and ME not in text, text
    assert pseudonym(ME) in text


async def test_an_admin_target_username_stays_out_of_the_logs(db, caplog):
    from app.services.admin import handle_admin_command

    await db["usernames"].insert_one({"_id": "hamid", "user_id": ME})
    caplog.set_level(logging.INFO)
    await handle_admin_command("/limit @Hamid 30", str(get_settings().admin_chat_id or 1))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "admin" in text and "limit" in text, text  # the audit line was written
    assert "hamid" not in text.lower() and ME not in text and pseudonym(ME) in text, text


async def test_logid_gives_the_admin_the_pseudonym_to_search_for(db):
    from app.services.admin import ADMIN_COMMANDS, handle_admin_command

    await db["usernames"].insert_one({"_id": "hamid", "user_id": ME})
    assert "/logid" in ADMIN_COMMANDS
    assert pseudonym(ME) in await handle_admin_command("/logid @hamid", "1")
    assert pseudonym(ME) in await handle_admin_command(f"/logid {ME}", "1")
    assert "u:" not in await handle_admin_command("/logid @nobody", "1")


def test_no_log_call_writes_a_title():
    calls = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"logger\.\w+\(\s*\"([^\"]*)\"", text):
            if "title" in match.group(1):
                calls.append(f"{path.relative_to(ROOT)}: {match.group(1)}")
    assert not calls, calls
