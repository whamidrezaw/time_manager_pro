from __future__ import annotations

import pytest

from app.schemas.requests import ListEventsRequest
from app.services.events import build_list_query
from app.utils.text import normalize_digits, regex_clause, search_variants


def _request(**overrides) -> ListEventsRequest:
    return ListEventsRequest(initData="dummy", **overrides)


# ── Term handling ───────────────────────────────────────────────────────────

def test_normalize_digits_maps_persian_and_arabic_forms() -> None:
    assert normalize_digits("۱۴۰۵/۰۱/۳۱") == "1405/01/31"
    assert normalize_digits("٢٠٢٦") == "2026"
    assert normalize_digits("plain") == "plain"


def test_search_variants_returns_both_spellings_only_when_they_differ() -> None:
    assert search_variants("۳") == ["۳", "3"]
    assert search_variants("meeting") == ["meeting"]
    assert search_variants("   ") == []


def test_regex_clause_escapes_the_term() -> None:
    """An unescaped term is a query-injection surface and a way to hand the
    database a pathological pattern."""
    clause = regex_clause("title", "a.*b(")
    assert clause["title"]["$regex"] == r"a\.\*b\("
    assert clause["title"]["$options"] == "i"


def test_regex_clause_can_be_case_sensitive() -> None:
    assert "$options" not in regex_clause("date_iso", "2026", case_insensitive=False)["date_iso"]


# ── Query building ──────────────────────────────────────────────────────────

def test_plain_list_query_is_scoped_to_the_user() -> None:
    query = build_list_query("42", _request())
    assert query == {"user_id": "42"}


def test_pinned_filter() -> None:
    assert build_list_query("42", _request(filter="pinned"))["pinned"] is True


def test_category_filter() -> None:
    assert build_list_query("42", _request(filter="birthday"))["category"] == "birthday"


def test_search_adds_an_or_over_the_text_fields() -> None:
    query = build_list_query("42", _request(q="mom"))
    fields = {key for clause in query["$or"] for key in clause}

    assert query["user_id"] == "42"
    assert {"title", "note", "date_iso", "date_jalali"} <= fields


def test_search_matches_category_names_too() -> None:
    """The old client-side search matched the category label; keep that."""
    query = build_list_query("42", _request(q="birth"))
    categories = [c["category"]["$in"] for c in query["$or"] if "category" in c]

    assert categories == [["birthday"]]


def test_search_covers_both_digit_spellings() -> None:
    query = build_list_query("42", _request(q="۱۴۰۵"))
    patterns = {
        clause["date_jalali"]["$regex"]
        for clause in query["$or"]
        if "date_jalali" in clause
    }

    assert patterns == {"۱۴۰۵", "1405"}


def test_search_and_filter_combine() -> None:
    query = build_list_query("42", _request(q="mom", filter="pinned"))
    assert query["pinned"] is True
    assert query["$or"]


def test_list_request_rejects_an_unknown_filter() -> None:
    with pytest.raises(ValueError):
        _request(filter="'; drop everything")
