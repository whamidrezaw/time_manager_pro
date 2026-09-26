"""The test data agrees with itself (Batch 22).

seed_event moved date_iso when a test asked for another day but kept its
default date_jalali, so a seeded card could read 2026-09-27 beside
1405/06/29, seven days apart, and a check of the Jalali date would have
tested the fixture instead of the app.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.utils.dates import to_jalali
from tests.harness import install_fake_db, seed_event, teardown_fake_db


@pytest.fixture
def fake_db():
    install_fake_db()
    yield
    teardown_fake_db()


async def test_a_seeded_event_carries_the_jalali_date_of_its_own_day(fake_db):
    when = (date.today() + timedelta(days=3)).isoformat()
    event = await seed_event(user_id="1", date_iso=when)

    assert event["date_jalali"] == to_jalali(when), (event["date_iso"], event["date_jalali"])


async def test_an_explicit_jalali_date_is_left_as_given(fake_db):
    event = await seed_event(user_id="1", date_iso="2026-09-20", date_jalali="1405/06/29")

    assert event["date_jalali"] == "1405/06/29"
