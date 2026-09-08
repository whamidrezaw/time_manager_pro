from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routes.telegram import parse_command, parse_start_payload
from app.services.referrals import (
    CODE_LENGTH,
    STATUS_PENDING,
    STATUS_VALID,
    build_invite_link,
    compute_event_limit,
    generate_ref_code,
    invites_to_next_bonus,
    normalize_code,
    parse_ref_payload,
)

# The reward maths must not move when someone edits the .env, so every test
# that asserts a number brings its own settings rather than reading the real
# ones. The defaults here are the product decision: 20, +20 per 3, cap 500.
SETTINGS = SimpleNamespace(
    event_limit_base=20,
    referral_step=3,
    referral_bonus=20,
    max_events_per_user=500,
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


# ── Codes ────────────────────────────────────────────────────────────

def test_generated_codes_are_unambiguous_and_unique() -> None:
    codes = {generate_ref_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert len(code) == CODE_LENGTH
        # 0/O/1/I/L are excluded so a code survives being read out loud.
        assert not set(code) & set("01OIL")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("r_7KQ2M9XA", "7KQ2M9XA"),
        ("R_7kq2m9xa", "7KQ2M9XA"),
        ("ref_7KQ2M9XA", "7KQ2M9XA"),
        ("7KQ2M9XA", "7KQ2M9XA"),
        ("  r_7KQ2M9XA  ", "7KQ2M9XA"),
    ],
)
def test_normalize_code_accepts_every_shape_a_link_can_arrive_in(raw, expected) -> None:
    assert normalize_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", None, "r_", "r_SHORT", "r_7KQ2M9XAEXTRA", "r_0OIL1234", "e_507f1f77bcf86cd7"],
)
def test_normalize_code_rejects_anything_it_cannot_trust(raw) -> None:
    """A half-valid code is worse than none: it credits the wrong account."""
    assert normalize_code(raw) is None
    assert parse_ref_payload(raw) is None


def test_invite_link_opens_the_mini_app_directly() -> None:
    link = build_invite_link("7KQ2M9XA", SETTINGS)

    # startapp, not start: the invitee lands in the app, not in an empty chat.
    assert link == "https://t.me/Timemanager2026_bot/app?startapp=r_7KQ2M9XA"


# ── Reward maths ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("valid", "expected"),
    [(0, 20), (1, 20), (2, 20), (3, 40), (5, 40), (6, 60), (30, 220)],
)
def test_limit_rises_one_step_at_a_time(valid, expected) -> None:
    assert compute_event_limit(valid, SETTINGS) == expected


def test_limit_never_passes_the_hard_ceiling() -> None:
    # 24 completed steps reach 500; everything beyond has to stay there.
    assert compute_event_limit(72, SETTINGS) == 500
    assert compute_event_limit(500, SETTINGS) == 500


def test_negative_invite_counts_fall_back_to_the_base() -> None:
    assert compute_event_limit(-5, SETTINGS) == 20


@pytest.mark.parametrize(("valid", "expected"), [(0, 3), (1, 2), (2, 1), (3, 3), (4, 2)])
def test_countdown_to_the_next_bonus(valid, expected) -> None:
    assert invites_to_next_bonus(valid, SETTINGS) == expected


# ── Deep link parsing ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start r_7KQ2M9XA", "r_7KQ2M9XA"),
        ("/start@Timemanager2026_bot r_7KQ2M9XA", "r_7KQ2M9XA"),
        ("/start", ""),
        ("/start   ", ""),
        ("hello", ""),
        ("", ""),
    ],
)
def test_parse_start_payload(text, expected) -> None:
    assert parse_start_payload(text) == expected


def test_reading_the_payload_did_not_change_the_command() -> None:
    """parse_command still drops the payload — the two never share a job."""
    assert parse_command("/start r_7KQ2M9XA") == "/start"


# ── Attribution rules (fake collections, no database) ────────────────

def _matches(doc: dict, query: dict) -> bool:
    for key, condition in query.items():
        value = doc.get(key)
        if isinstance(condition, dict) and "$exists" in condition:
            if (value is not None) != condition["$exists"]:
                return False
        elif not isinstance(condition, dict) and value != condition:
            return False
    return True


class FakeUsers:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = {doc["_id"]: dict(doc) for doc in (docs or [])}

    async def find_one(self, query, projection=None, sort=None):
        if "ref_code" in query:
            for doc in self.docs.values():
                if doc.get("ref_code") == query["ref_code"]:
                    return dict(doc)
            return None
        doc = self.docs.get(query.get("_id"))
        return dict(doc) if doc and _matches(doc, query) else None

    async def update_one(self, query, update, upsert=False):
        doc = self.docs.get(query.get("_id"))
        if doc is not None and _matches(doc, query):
            doc.update(update.get("$set", {}))
            return SimpleNamespace(modified_count=1, upserted_id=None)
        if doc is None and upsert:
            new = {"_id": query["_id"]}
            new.update(update.get("$setOnInsert", {}))
            new.update(update.get("$set", {}))
            self.docs[new["_id"]] = new
            return SimpleNamespace(modified_count=0, upserted_id=new["_id"])
        return SimpleNamespace(modified_count=0, upserted_id=None)

    async def find_one_and_update(self, query, update):
        doc = self.docs.get(query.get("_id"))
        if doc is None or not _matches(doc, query):
            return None
        before = dict(doc)
        doc.update(update.get("$set", {}))
        return before

    async def count_documents(self, query, **kwargs):
        return sum(1 for doc in self.docs.values() if _matches(doc, query))


class FakeEvents:
    def __init__(self, per_user: dict[str, int] | None = None) -> None:
        self.per_user = per_user or {}

    async def count_documents(self, query, **kwargs):
        return self.per_user.get(query.get("user_id"), 0)


@pytest.fixture
def referrals(monkeypatch):
    """The service with both collections swapped for in-memory fakes."""
    from app.services import referrals as module

    users = FakeUsers(
        [
            {"_id": "100", "ref_code": "AAAA2222"},
            {"_id": "200", "ref_code": "BBBB3333"},
        ]
    )
    events = FakeEvents()

    monkeypatch.setattr(module, "get_users_collection", lambda: users)
    monkeypatch.setattr(module, "get_events_collection", lambda: events)

    return SimpleNamespace(module=module, users=users, events=events)


@pytest.mark.anyio
async def test_a_valid_invite_is_recorded_as_pending(referrals) -> None:
    assert await referrals.module.attach_referrer("300", "BBBB3333") is True

    invitee = referrals.users.docs["300"]
    assert invitee["referred_by"] == "200"
    assert invitee["referral_status"] == STATUS_PENDING


@pytest.mark.anyio
async def test_you_cannot_invite_yourself(referrals) -> None:
    assert await referrals.module.attach_referrer("100", "AAAA2222") is False
    assert "referred_by" not in referrals.users.docs["100"]


@pytest.mark.anyio
async def test_an_unknown_code_credits_nobody(referrals) -> None:
    assert await referrals.module.attach_referrer("300", "ZZZZ9999") is False


@pytest.mark.anyio
async def test_first_touch_wins_and_cannot_be_overwritten(referrals) -> None:
    await referrals.module.attach_referrer("300", "BBBB3333")

    assert await referrals.module.attach_referrer("300", "AAAA2222") is False
    assert referrals.users.docs["300"]["referred_by"] == "200"


@pytest.mark.anyio
async def test_an_established_account_cannot_be_retro_attributed(referrals) -> None:
    """Someone who already has events opening a link is not a new user."""
    referrals.events.per_user["300"] = 4

    assert await referrals.module.attach_referrer("300", "BBBB3333") is False


@pytest.mark.anyio
async def test_the_invite_turns_valid_on_the_first_event_only_once(referrals) -> None:
    await referrals.module.attach_referrer("300", "BBBB3333")

    first = await referrals.module.activate_referral_if_first_event("300")
    assert referrals.users.docs["300"]["referral_status"] == STATUS_VALID
    assert first is None  # 1 of 3 — no bonus yet, so nothing to announce

    # The user's second event must not be able to count the same invite again.
    assert await referrals.module.activate_referral_if_first_event("300") is None
    assert await referrals.module.count_valid_invites("200") == 1


@pytest.mark.anyio
async def test_the_third_valid_invite_reports_a_bonus(referrals, monkeypatch) -> None:
    monkeypatch.setattr(referrals.module, "get_settings", lambda: SETTINGS)

    for invitee in ("301", "302", "303"):
        await referrals.module.attach_referrer(invitee, "BBBB3333")

    assert await referrals.module.activate_referral_if_first_event("301") is None
    assert await referrals.module.activate_referral_if_first_event("302") is None
    assert await referrals.module.activate_referral_if_first_event("303") == ("200", 3)

    assert await referrals.module.effective_event_limit("200", SETTINGS) == 40
