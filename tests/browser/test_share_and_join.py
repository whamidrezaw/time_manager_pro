"""The share sheet and the join card (Batch 22: the open findings).

Both showed the event's picture with alt="" and loaded it from
WEBAPP_BASE_URL, which the page's CSP refuses when that is not the page's own
address. A second join said so in an alert(), which a Telegram WebView may
never show. And the seven smaller dialogs had no axe check of their own.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.browser.test_a11y_browser import describe_violations
from tests.browser.test_a11y_dialogs import open_dialog

pytestmark = pytest.mark.browser


def test_the_share_preview_names_its_picture_and_loads_it_from_this_page(open_app):
    page = open_dialog(open_app, "share")[0]
    image = page.locator("#shrPreview img")
    expect(image).to_be_visible(timeout=8000)

    alt, src = image.get_attribute("alt") or "", image.get_attribute("src") or ""
    assert "Mom's birthday" in alt and "left" in alt, f"alt is {alt!r}"
    assert src.startswith("/c/"), f"src is {src!r}"


def test_the_join_card_names_its_picture_and_loads_it_from_this_page(open_app):
    page = open_app(invite=True, invite_card=True)
    image = page.locator(".invite-image")
    expect(image).to_be_visible(timeout=5000)

    alt, src = image.get_attribute("alt") or "", image.get_attribute("src") or ""
    assert "Book club" in alt and "left" in alt, f"alt is {alt!r}"
    assert src.startswith("/c/"), f"src is {src!r}"


def test_joining_an_event_you_already_have_says_so_in_the_app(open_app):
    page = open_app(invite=True)
    alerts: list[str] = []
    page.on("dialog", lambda dialog: (alerts.append(dialog.message), dialog.dismiss()))
    page.click("#inviteYes")
    expect(page.locator(".invite-overlay")).to_be_hidden(timeout=5000)

    page.reload()  # the same link, a second time
    expect(page.locator(".invite-overlay")).to_be_visible(timeout=5000)
    page.click("#inviteYes")

    expect(page.locator("#toast")).to_contain_text("You already have this one.", timeout=5000)
    assert not alerts, f"a browser dialog was used: {alerts}"


SMALLER_DIALOGS = ["onboarding", "picker", "day", "share", "referral", "invite", "confirm-over-detail"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", SMALLER_DIALOGS)
def test_axe_finds_nothing_serious_in_the_smaller_dialogs(open_app, axe, name, scheme):
    def themed(**kwargs):
        return open_app(scheme=scheme, **kwargs)

    page = open_dialog(themed, name)[0]
    page.wait_for_timeout(400)

    violations = axe(page)
    assert not violations, describe_violations(violations)
