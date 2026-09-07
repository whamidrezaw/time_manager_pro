from __future__ import annotations

import re

# Persian and Arabic-Indic digits map onto ASCII so that a date typed on a
# Persian keyboard matches a date_jalali stored as "1405/01/31".
_DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_digits(value: str) -> str:
    return (value or "").translate(_DIGIT_MAP)


def search_variants(term: str) -> list[str]:
    """The forms of a search term worth matching against.

    A user may type "۳" while the stored title reads "3", or the other way
    round, so both spellings are tried whenever they differ.
    """
    term = (term or "").strip()
    if not term:
        return []

    normalized = normalize_digits(term)
    return [term] if normalized == term else [term, normalized]


def regex_clause(field: str, term: str, case_insensitive: bool = True) -> dict:
    """A MongoDB regex clause with the term escaped.

    Escaping is not cosmetic: an unescaped term is both a query-injection
    surface and a way to hand the database a pathological pattern that pins its
    CPU for as long as the attacker keeps sending it.
    """
    clause: dict = {"$regex": re.escape(term)}
    if case_insensitive:
        clause["$options"] = "i"
    return {field: clause}
