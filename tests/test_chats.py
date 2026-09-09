from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chats import (
    ALLOWED_TYPES,
    chat_link,
    generate_link_token,
    parse_chat_payload,
    scope_for,
)

SETTINGS = SimpleNamespace(
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)


def test_tokens_are_unique_and_url_safe() -> None:
    tokens = {generate_link_token() for _ in range(200)}

    assert len(tokens) == 200
    assert all(all(c.isalnum() or c in "-_" for c in token) for token in tokens)


def test_the_chat_link_opens_the_mini_app() -> None:
    assert chat_link("abcdefghijkl", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=g_abcdefghijkl"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("g_abcdefghijkl", "abcdefghijkl"),
        ("G_abcdefghijkl", "abcdefghijkl"),
        ("s_abcdefghijkl", None),        # a shared event
        ("r_7KQ2M9XA", None),            # a referral
        ("e_abc123DEF456ghi789", None),  # a public countdown
        ("g_short", None),
        ("", None),
        (None, None),
    ],
)
def test_four_kinds_of_deep_link_stay_out_of_each_other(payload, expected) -> None:
    assert parse_chat_payload(payload) == expected


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [("group", "group"), ("supergroup", "group"), ("channel", "channel"),
     ("private", "private"), (None, "private")],
)
def test_scope_follows_the_chat_type(chat_type, expected) -> None:
    assert scope_for(chat_type) == expected


def test_a_private_chat_is_never_a_destination() -> None:
    """It is where reminders already go; offering it would send two copies."""
    assert "private" not in ALLOWED_TYPES
    assert {"group", "supergroup", "channel"} == ALLOWED_TYPES
