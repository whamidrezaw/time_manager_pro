from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.share_group import (
    SHARED_FIELDS,
    generate_share_token,
    invite_url,
    parse_share_payload,
)

SETTINGS = SimpleNamespace(
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


def test_tokens_are_long_and_unique() -> None:
    tokens = {generate_share_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 12
        assert all(c.isalnum() or c in "-_" for c in token)


def test_the_invite_link_opens_the_mini_app() -> None:
    assert invite_url("abcdefghijkl", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=s_abcdefghijkl"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("s_abcdefghijkl", "abcdefghijkl"),
        ("S_abcdefghijkl", "abcdefghijkl"),
        ("r_7KQ2M9XA", None),          # a referral payload, not ours
        ("e_abc123DEF456ghi789", None),  # a public countdown payload
        ("s_short", None),
        ("s_bad token", None),
        ("", None),
        (None, None),
    ],
)
def test_only_our_own_share_payloads_resolve(payload, expected) -> None:
    """Three kinds of deep link now share one entry point, and each has to
    keep its hands off the other two."""
    assert parse_share_payload(payload) == expected


def test_the_shared_set_is_what_the_event_is_and_when() -> None:
    """The split is the whole design: everything personal stays out of it."""
    assert set(SHARED_FIELDS) == {
        "title", "date_iso", "date_jalali", "all_day", "time_hm",
        "repeat", "repeat_until", "category",
    }
    for personal in ("note", "pinned", "tz_name", "reminders",
                     "reminder_hour", "lead_repeat", "user_id"):
        assert personal not in SHARED_FIELDS
