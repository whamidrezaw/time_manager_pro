"""The event-limit nudge, as decided in Batch 20.

It used to appear at 80 % of the limit ("Running out of space") and sit on the
list from then on. It now appears only once the limit is reached, says so, and
offers invites, which raise it; below the limit the list keeps that room.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.config import get_settings

pytestmark = pytest.mark.browser

DEFAULT_EVENTS = 3  # every test user starts with these (conftest.default_events)


def test_the_nudge_stays_away_until_the_limit_is_reached(open_app):
    page = open_app(extra_events=get_settings().event_limit_base - DEFAULT_EVENTS - 1)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)

    expect(page.locator("#refNudge")).to_have_count(0)


def test_at_the_limit_the_nudge_says_so_and_offers_invites(open_app):
    page = open_app(extra_events=get_settings().event_limit_base - DEFAULT_EVENTS)
    nudge = page.locator("#refNudge")

    expect(nudge).to_be_visible(timeout=4000)
    expect(nudge).to_contain_text("You've reached your event limit")
    expect(nudge.locator("button")).to_have_text("Invite now")
