"""A11Y-02: confirmations and the detail page's destructive actions.

Critical findings from the Batch 20 review. They live in their own file
because they ship to production as a hotfix ahead of the rest of the batch:
this file is byte-identical on main and on fix/batch20-a11y, so merging main
back into the batch branch stays conflict-free.

Every test here failed against main at cdeddfb, reproduced in Chromium.
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

FAST = 1500  # ms


def eventually(page, check, seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            return False
        page.wait_for_timeout(50)
    return True


def activate_row_delete(page, title: str) -> None:
    """Presses a row's Delete exactly as the end of a swipe-and-tap does."""
    page.evaluate("""(title) => [...document.querySelectorAll('.event-row .row-action-delete')]
        .find((b) => b.getAttribute('aria-label').endsWith(title)).click()""", title)


def open_detail(page) -> None:
    page.focus(".event-card >> nth=0")
    page.keyboard.press("Enter")
    expect(page.locator("#detailSheet")).to_be_visible()


def test_a_cancelled_delete_is_not_replayed_by_the_next_confirmation(open_app):
    """Dismissing the confirm without its buttons left its OK handler armed.

    showConfirm listened for Escape with {once: true}, so any earlier key (Tab)
    used the listener up. The global Escape handler then hid the overlay
    without settling the promise, and the next OK click resolved it as well:
    deleting one event also deleted the one the user had just cancelled.
    """
    page = open_app()
    activate_row_delete(page, "Mom's birthday")
    page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    expect(page.locator("#confirmOverlay")).to_be_hidden(timeout=FAST)

    activate_row_delete(page, "Tax return")
    page.click("#confirmOkBtn")
    expect(page.locator(".event-title", has_text="Tax return")).to_have_count(0)

    cancelled = page.event_ids["Mom's birthday"]
    assert cancelled not in page.deleted, "the delete the user cancelled was sent anyway"
    assert page.deleted == [page.event_ids["Tax return"]], f"DELETE requests sent: {len(page.deleted)}"


def test_one_confirmation_sends_exactly_one_delete(open_app):
    """A second activation stacked a second pending confirm.

    Each press added another OK listener, and one OK then fired them all: the
    same event was deleted twice and the second call failed.
    """
    page = open_app()
    activate_row_delete(page, "Tax return")
    activate_row_delete(page, "Tax return")
    page.click("#confirmOkBtn")
    expect(page.locator(".event-title", has_text="Tax return")).to_have_count(0)
    page.wait_for_timeout(300)

    assert page.deleted == [page.event_ids["Tax return"]], f"DELETE requests sent: {len(page.deleted)}"


def test_the_detail_page_delete_button_asks_before_deleting(open_app):
    """The button did nothing at all, for every user.

    addEventListener passed the click event as deleteCurrentEvent's first
    argument, so the `eventId = state.detailEventId` default never applied and
    getEventById(MouseEvent) returned null. Keyboard users had no other way to
    delete: the swipe actions are tabIndex -1 by design.
    """
    page = open_app()
    open_detail(page)
    page.click("#detailDeleteBtn")

    expect(page.locator("#confirmOverlay")).to_be_visible(timeout=FAST)


def test_the_detail_page_pin_button_sends_the_change(open_app):
    """Same defect as Delete: toggleCurrentPin received the click event."""
    page = open_app()
    open_detail(page)
    page.click("#detailPinBtn")

    assert eventually(page, lambda: "pin" in page.api_calls), (
        f"pressing Pin sent nothing; API calls after load: {page.api_calls}"
    )


def test_a_cancelled_delete_stays_cancelled_without_native_dialog_support(open_app):
    """The same guarantee on WebViews without <dialog> (iOS before 15.4).

    showConfirm falls back to the open attribute and its own Escape handling
    there. A fallback that only runs on old phones is exactly the branch nobody
    would notice breaking, so it is pinned here with the dialog API removed.
    """
    page = open_app()
    page.add_init_script(
        "delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close;"
    )
    page.reload()
    page.wait_for_selector(".event-card")

    activate_row_delete(page, "Mom's birthday")
    expect(page.locator("#confirmOverlay")).to_be_visible(timeout=FAST)
    page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    expect(page.locator("#confirmOverlay")).to_be_hidden(timeout=FAST)

    activate_row_delete(page, "Tax return")
    page.click("#confirmOkBtn")
    expect(page.locator(".event-title", has_text="Tax return")).to_have_count(0)

    assert page.deleted == [page.event_ids["Tax return"]], f"DELETE requests sent: {len(page.deleted)}"
