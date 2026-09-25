"""The compact top card (Batch 21: option A, with its icons, as chosen).

On a 390 x 844 phone the card was 231 px tall and left room for a single
event above the tab bar. It keeps its heading and both statistics with their
icons, and gives the rest of the screen to the list.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_two_whole_events_are_on_screen_without_scrolling(open_app):
    page = open_app()  # 390 x 844, with the three default events
    page.wait_for_timeout(300)
    layout = page.evaluate("""() => {
        const bottom = document.querySelector('.tabbar').getBoundingClientRect().top;
        const cards = [...document.querySelectorAll('.event-card')].map((c) => c.getBoundingClientRect());
        const whole = cards.filter((r) => r.bottom <= bottom).length;
        return { first: cards[0].top, height: innerHeight, whole };
    }""")

    # The old card put the first event at 58 % of the screen; the compact one at
    # 40 %. The bar is 45 %: 40 % would pass by 0.02 px, and a font one pixel
    # taller on another system would fail it. The margin that matters is below.
    assert layout["first"] <= 0.45 * layout["height"], f"the first event starts at {layout['first']:.0f} px"
    assert layout["whole"] >= 2, f"only {layout['whole']} whole event(s) above the tab bar"


def test_the_card_keeps_its_heading_and_both_statistics_with_icons(open_app):
    page = open_app()
    card = page.locator(".hero-card")

    expect(card.locator("h1")).to_have_text("Stay on top of every moment")
    expect(card.locator("#eventCount")).to_have_text("3")
    expect(card.locator("#syncStatus")).to_be_visible()
    icons = card.locator(".stat-icon")
    expect(icons).to_have_text(["📅", "🔔"])
    for i in range(2):
        expect(icons.nth(i)).to_be_visible()
        expect(icons.nth(i)).to_have_attribute("aria-hidden", "true")  # decorative; the label names it
    expect(card.locator(".hero-badge, .hero-text")).to_have_count(0)
