from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.cards import days_until, fonts_available, render_event_card
from app.services.sharing import (
    EVENT_PREFIX,
    card_url,
    generate_public_token,
    miniapp_url,
    parse_event_payload,
    public_url,
)
from app.services.telegram_api import build_photo_result

SETTINGS = SimpleNamespace(
    webapp_base_url="https://tm.example.com",
    telegram_bot_username="Timemanager2026_bot",
    telegram_mini_app_short_name="app",
)

needs_fonts = pytest.mark.skipif(
    not fonts_available(),
    reason="static/fonts is empty — run apply_batch12b.py or fetch Vazirmatn",
)


def _event(**overrides) -> dict:
    base = {
        "title": "Mum's birthday",
        "date_iso": "2026-10-20",
        "date_jalali": "1405/07/28",
        "category": "birthday",
        "all_day": True,
        "tz_name": "Europe/Berlin",
        "lang": "en",
        "bot_handle": "@Timemanager2026_bot",
    }
    base.update(overrides)
    return base


# ── Tokens and links ─────────────────────────────────────────────────

def test_tokens_are_long_and_unique() -> None:
    tokens = {generate_public_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 16
        assert all(char.isalnum() or char in "-_" for char in token)


def test_link_shapes() -> None:
    assert public_url("abc123DEF456ghi789", SETTINGS) == (
        "https://tm.example.com/c/abc123DEF456ghi789"
    )
    assert card_url("abc123DEF456ghi789", SETTINGS).endswith("/card.png")
    assert miniapp_url("abc123DEF456ghi789", SETTINGS) == (
        "https://t.me/Timemanager2026_bot/app?startapp=e_abc123DEF456ghi789"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (EVENT_PREFIX + "abc123DEF456ghi789", "abc123DEF456ghi789"),
        ("e_abc123DEF456ghi789", "abc123DEF456ghi789"),
        ("r_7KQ2M9XA", None),  # a Batch 12a referral payload, not ours
        ("e_short", None),
        ("e_bad token here", None),
        ("", None),
        (None, None),
    ],
)
def test_only_our_own_event_payloads_resolve(payload, expected) -> None:
    assert parse_event_payload(payload) == expected


# ── Countdown maths ──────────────────────────────────────────────────

def _now(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("today", "expected"),
    [("2026-10-18T09:00", 2), ("2026-10-20T09:00", 0), ("2026-10-25T09:00", -5)],
)
def test_days_until_counts_calendar_days(today, expected) -> None:
    assert days_until(_event(), _now(today)) == expected


def test_a_late_evening_event_is_still_tomorrow() -> None:
    """Comparing instants would call 23:30 tomorrow "0 days" before midnight."""
    event = _event(date_iso="2026-10-20", all_day=False, time_hm="23:30")

    assert days_until(event, _now("2026-10-19T00:30")) == 1


def test_the_next_occurrence_wins_over_the_original_date() -> None:
    """For a repeating event, event_ts_utc already holds the next one."""
    event = _event(
        date_iso="2020-10-20",
        repeat="yearly",
        event_ts_utc=datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc),
    )

    assert days_until(event, _now("2026-10-13T09:00")) == 7


def test_a_broken_date_does_not_take_the_card_down() -> None:
    assert days_until(_event(date_iso="not-a-date")) == 0


# ── The card itself ──────────────────────────────────────────────────

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@needs_fonts
@pytest.mark.parametrize("language", ["en", "fa"])
def test_card_renders_a_real_png(language) -> None:
    png = render_event_card(_event(), language)

    assert png.startswith(PNG_MAGIC)
    assert len(png) > 10_000


@needs_fonts
def test_a_very_long_title_is_truncated_rather_than_overflowing() -> None:
    long_title = "Quarterly planning review with the platform and infrastructure teams " * 3

    assert render_event_card(_event(title=long_title), "en").startswith(PNG_MAGIC)


@needs_fonts
@pytest.mark.parametrize(
    "event",
    [
        _event(title=""),
        _event(date_jalali=""),
        _event(all_day=False, time_hm="18:30"),
        _event(event_ts_utc=datetime.now(timezone.utc) + timedelta(days=400)),
        _event(event_ts_utc=datetime.now(timezone.utc) - timedelta(days=3)),
        _event(category="unknown-category"),
    ],
)
def test_awkward_events_still_produce_a_card(event) -> None:
    assert render_event_card(event, "fa").startswith(PNG_MAGIC)


# ── The inline result handed to Telegram ─────────────────────────────

def test_every_shared_card_carries_a_way_back_to_the_bot() -> None:
    result = build_photo_result(
        card="https://tm.example.com/c/tok/card.png",
        link="https://t.me/Timemanager2026_bot/app?startapp=e_tok",
        title="Mum's birthday",
        caption="<b>Mum's birthday</b>",
        button="Open in TimeManager",
    )

    assert result["type"] == "photo"
    assert result["photo_url"].endswith("/card.png")
    assert result["thumbnail_url"] == result["photo_url"]

    # The whole point of Batch 12: a shared card is a door back in.
    button = result["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"].startswith("https://t.me/")
    assert "startapp=" in button["url"]
