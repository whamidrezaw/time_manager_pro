"""Code health from the five-axis review (Batch 29, R3 to R5).

R3: a service imported a route, and cards reached into the reminder service,
each hidden in a function to dodge an import cycle. R4: app/deps.py was
imported by nothing. R5: three `except Exception` turned any bug into a
default value; each now catches only what it expects, and the proof is an
unexpected error that has to come through.
"""
from __future__ import annotations

import ast
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_services_never_import_routes():
    offenders = [f"{path.relative_to(ROOT)}: {name}" for path in (APP / "services").rglob("*.py")
                 for name in imports_of(path) if name.startswith("app.routes")]
    assert not offenders, offenders


def test_cards_do_not_reach_into_the_reminder_service():
    cards = imports_of(APP / "services" / "cards.py")
    reaching = {name for name in cards if name.startswith("app.services.reminders")}
    assert not reaching, reaching


def test_every_module_is_used():
    modules = {".".join(p.relative_to(ROOT).with_suffix("").parts)
               for p in APP.rglob("*.py") if p.name != "__init__.py"}
    used: set[str] = set()
    for path in list(APP.rglob("*.py")) + list((ROOT / "worker").rglob("*.py")):
        used |= imports_of(path)
    unused = modules - used - {"app.main"}  # the ASGI entry point, started by gunicorn
    assert not unused, sorted(unused)


def test_a_bad_timezone_falls_back_but_a_bug_is_not_hidden(monkeypatch):
    import app.services.cards as cards

    assert cards._zone("Not/A_Zone") == timezone.utc

    def broken(name):
        raise RuntimeError("a bug, not a bad timezone")

    monkeypatch.setattr(cards, "ZoneInfo", broken)
    with pytest.raises(RuntimeError):
        cards._zone("Europe/Berlin")


def test_a_bad_date_is_returned_as_is_but_a_bug_is_not_hidden(monkeypatch):
    from app.utils import dates

    assert dates.to_jalali("not-a-date") == "not-a-date"

    class Broken:
        class date:
            @staticmethod
            def fromgregorian(**kwargs):
                raise RuntimeError("a bug, not a bad date")

    monkeypatch.setattr(dates, "jdatetime", Broken)
    with pytest.raises(RuntimeError):
        dates.to_jalali("2026-09-26")


def test_a_bad_id_is_false_but_a_bug_is_not_hidden(monkeypatch):
    from app.utils import ids

    assert ids.is_valid_object_id("nope") is False and ids.is_valid_object_id(12345) is False

    def broken(raw):
        raise RuntimeError("a bug, not a bad id")

    monkeypatch.setattr(ids, "ObjectId", broken)
    with pytest.raises(RuntimeError):
        ids.is_valid_object_id("0" * 24)
