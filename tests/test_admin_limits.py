"""Admin control over event limits (Batch 20), as designed and approved.

The admin is ADMIN_CHAT_ID and nothing else. From the bot they give one user a
limit of their own, or none, and change the base limit and the invite reward
at any time; values left unset fall back to the environment's. Unlimited still
stops at the technical ceiling. Every change is audited.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import get_database, get_events_collection
from tests.harness import install_fake_db, teardown_fake_db

ADMIN = "999000111"
USER = "123456789"


@pytest.fixture
def fake_db():
    install_fake_db()
    yield
    teardown_fake_db()


@pytest.fixture
def settings(fake_db):
    return get_settings().model_copy(update={"admin_chat_id": ADMIN})


def admin_module():
    try:
        import app.services.admin as admin
    except ImportError:
        pytest.fail("app/services/admin.py does not exist yet")
    return admin


async def limit_of(user_id, settings):
    from app.services.referrals import effective_event_limit
    return await effective_event_limit(user_id, settings)


async def test_only_the_admin_chat_id_is_an_admin(settings):
    admin = admin_module()
    assert admin.is_admin(ADMIN, settings) and not admin.is_admin(USER, settings)
    assert not admin.is_admin(ADMIN, settings.model_copy(update={"admin_chat_id": ""}))


async def test_the_admin_account_is_unlimited_up_to_the_ceiling(settings):
    admin_module()
    assert await limit_of(ADMIN, settings) == settings.max_events_per_user


async def test_a_custom_limit_by_id(settings):
    admin = admin_module()
    reply = await admin.handle_admin_command(f"/limit {USER} 100", ADMIN, settings)
    assert "100" in reply
    assert await limit_of(USER, settings) == 100


async def test_a_custom_limit_by_username(settings):
    admin = admin_module()
    await admin.remember_username(USER, "Sara_K")
    await admin.handle_admin_command("/limit @sara_k 40", ADMIN, settings)
    assert await limit_of(USER, settings) == 40


async def test_an_unknown_username_is_reported_not_guessed(settings):
    admin = admin_module()
    reply = await admin.handle_admin_command("/limit @nobody_here 40", ADMIN, settings)
    assert "not found" in reply.lower()
    assert await get_database()["admin_audit"].count_documents({}) == 0


async def test_unlimited_stops_at_the_ceiling_and_default_goes_back(settings):
    admin = admin_module()
    await admin.handle_admin_command(f"/limit {USER} unlimited", ADMIN, settings)
    assert await limit_of(USER, settings) == settings.max_events_per_user
    assert (await admin.limit_status(USER, settings))["unlimited"] is True
    await admin.handle_admin_command(f"/limit {USER} default", ADMIN, settings)
    assert await limit_of(USER, settings) == settings.event_limit_base


async def test_the_base_limit_changes_at_once_and_can_go_back(settings):
    admin = admin_module()
    await admin.handle_admin_command("/setbase 30", ADMIN, settings)
    assert await limit_of(USER, settings) == 30
    await admin.handle_admin_command("/setbase default", ADMIN, settings)
    assert await limit_of(USER, settings) == settings.event_limit_base


@pytest.mark.parametrize("text", ["/limit 123 abc", "/limit 123 0", "/limit 123 100000", "/setbase 0",
                                  "/setbase lots", "/setstep 0", "/setbonus -1", "/limit"])
async def test_invalid_input_changes_nothing(settings, text):
    admin = admin_module()
    reply = await admin.handle_admin_command(text, ADMIN, settings)
    assert reply
    assert await limit_of("123", settings) == settings.event_limit_base
    assert await get_database()["admin_audit"].count_documents({}) == 0


async def test_every_change_is_audited(settings):
    admin = admin_module()
    await admin.handle_admin_command(f"/limit {USER} 70", ADMIN, settings)
    await admin.handle_admin_command("/setbonus 10", ADMIN, settings)
    entries = await get_database()["admin_audit"].find({}).sort("at", 1).to_list(None)
    assert [(e["admin"], e["action"]) for e in entries] == [(ADMIN, "limit"), (ADMIN, "setbonus")]
    assert entries[0]["target"] == USER and entries[0]["after"] == 70


async def test_limits_shows_the_settings(settings):
    admin = admin_module()
    await admin.handle_admin_command("/setbase 30", ADMIN, settings)
    reply = await admin.handle_admin_command("/limits", ADMIN, settings)
    assert "30" in reply and str(settings.max_events_per_user) in reply


async def test_a_limit_below_the_usage_warns(settings):
    admin = admin_module()
    await get_events_collection().insert_many([{"user_id": USER, "title": f"e{n}"} for n in range(5)])
    reply = await admin.handle_admin_command(f"/limit {USER} 3", ADMIN, settings)
    assert "5" in reply and "3" in reply
