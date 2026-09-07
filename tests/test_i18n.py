from __future__ import annotations

import pytest

from app.services.reminders import build_reminder_text, event_language
from app.utils.i18n import (
    DEFAULT_LANGUAGE,
    category_label,
    repeat_label,
    resolve_language,
    t,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("fa", "fa"),
        ("fa-IR", "fa"),
        ("FA", "fa"),
        ("de", "en"),
        ("es", "en"),
        ("en-GB", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_resolve_language(code, expected) -> None:
    """Persian for the audience the Jalali calendar is here for, English for
    everyone else — a German speaker is better served by English than Persian."""
    assert resolve_language(code) == expected


def test_unknown_key_returns_the_key() -> None:
    assert t("no_such_key", "fa") == "no_such_key"


def test_missing_translation_falls_back_to_english() -> None:
    assert t("start", "de") == t("start", DEFAULT_LANGUAGE)


def test_labels_have_persian_forms() -> None:
    assert repeat_label("yearly", "fa") != repeat_label("yearly", "en")
    assert category_label("work", "fa") != category_label("work", "en")


def test_unknown_repeat_and_category_fall_back() -> None:
    assert repeat_label("nonsense", "en") == repeat_label("none", "en")
    assert category_label("nonsense", "en") == category_label("general", "en")


def _event(**overrides) -> dict:
    base = {
        "title": "Standup",
        "date_iso": "2026-04-20",
        "date_jalali": "1405/01/31",
        "repeat": "yearly",
        "category": "work",
        "pinned": False,
        "all_day": True,
    }
    base.update(overrides)
    return base


def test_event_language_defaults_to_english() -> None:
    """Documents written before the field existed carry no lang."""
    assert event_language(_event()) == "en"
    assert event_language(_event(lang=None)) == "en"


def test_reminder_text_follows_the_stored_language() -> None:
    persian = build_reminder_text(_event(lang="fa"))
    english = build_reminder_text(_event(lang="en"))

    assert repeat_label("yearly", "fa") in persian
    assert repeat_label("yearly", "en") in english
    assert persian != english


def test_reminder_text_escapes_the_title_in_both_languages() -> None:
    for lang in ("en", "fa"):
        text = build_reminder_text(_event(lang=lang, title="<script>x</script>"))
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
