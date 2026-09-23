"""A11Y-01: every dialog behaves like one.

One set of rules for all nine dialogs, checked in a real browser:

* opening a dialog puts focus on its title; the confirm starts at Cancel;
* Tab never leaves an open dialog;
* Escape closes it, and when two are stacked, Escape or Telegram's back
  button closes only the one on top;
* closing it hands focus back to whatever opened it.

Batch 20 moves the dialogs onto native <dialog> through static/modal.js in
three slices: 2a onboarding, the date picker and the day sheet; 2b the
composer and the detail page; 2c share, referral and invite, whose cases are
added at the start of 2c. Cases for a dialog whose slice has not landed yet
fail on purpose. Tests marked "Guard" passed before any migration: they pin
behaviour that already worked, so moving the code cannot quietly break it.
"""
from __future__ import annotations

import time
from datetime import date

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


def focus_is_inside(page, selector: str) -> bool:
    return page.evaluate("(s) => !!(document.activeElement && document.activeElement.closest(s))", selector)


def focused(page) -> str:
    return page.evaluate("""() => { const a = document.activeElement;
        if (!a) return 'nothing';
        const cls = a.className ? '.' + String(a.className).split(' ')[0] : '';
        return a.id ? '#' + a.id : a.tagName.toLowerCase() + cls; }""")


def ax_ignored(page, selector: str) -> bool:
    """Whether the accessibility tree drops this element, which is what a
    screen reader goes by. Everything outside an open modal dialog is inert."""
    cdp = page.context.new_cdp_session(page)
    document = cdp.send("DOM.getDocument", {"depth": 0})
    node = cdp.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
    tree = cdp.send("Accessibility.getPartialAXTree", {"nodeId": node["nodeId"], "fetchRelatives": False})
    return bool(tree["nodes"][0].get("ignored"))


def press_telegram_back(page) -> None:
    """What Telegram does when the user presses its back button."""
    page.evaluate("() => (window.__tgBackHandlers || []).slice().forEach((handler) => handler())")


def open_dialog(open_app, name: str):
    """Opens a dialog the way a user would. Returns (page, dialog, title) selectors."""
    if name == "onboarding":
        page = open_app(onboarding_seen=False)
        dialog, title = "#onboardingOverlay", "#onboardingTitle"
    else:
        page = open_app()
        if name == "composer":
            page.click("#openComposerBtn")
            dialog, title = "#composerSheet", "#composerTitle"
        elif name == "detail":
            page.focus(".event-card >> nth=0")
            page.keyboard.press("Enter")
            dialog, title = "#detailSheet", "#detailTitle"
        elif name == "picker":
            page.click("#openComposerBtn")
            expect(page.locator("#composerSheet")).to_be_visible()
            page.click("#date")
            dialog, title = "#dpOverlay", "#dpTitle"
        elif name == "day":
            page.click("[data-view=month]")
            page.focus(".cal-day.is-today")
            page.keyboard.press("Enter")
            dialog, title = ".day-dialog", "#dayTitle"
        elif name == "share":
            page.focus(".event-card >> nth=0")
            page.keyboard.press("Enter")
            expect(page.locator("#detailSheet")).to_be_visible()
            page.click("#detailShareBtn")
            dialog, title = ".shr-overlay", "#shrTitle"
        elif name == "confirm-over-detail":
            page.focus(".event-card >> nth=0")
            page.keyboard.press("Enter")
            expect(page.locator("#detailSheet")).to_be_visible()
            page.click("#detailDeleteBtn")
            dialog, title = "#confirmOverlay", "#confirmTitle"
        else:
            raise AssertionError(f"unknown dialog {name}")
    expect(page.locator(dialog)).to_be_visible()
    return page, dialog, title


# ── Focus on open ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["composer", "detail", "onboarding", "picker", "day", "share"])
def test_opening_a_dialog_focuses_its_title(open_app, name):
    """Decided in Batch 20: a dialog starts at its title.

    A screen reader announces where the user now is, and on a phone no keyboard
    springs up over half the form before anything was chosen. The picker used
    to start at Confirm, where a held Space could pick a date by accident.
    """
    page, _, title = open_dialog(open_app, name)

    assert eventually(page, lambda: focused(page) == title, 1.0), (
        f"{name} opened with focus on {focused(page)}"
    )


def test_the_confirm_starts_at_cancel(open_app):
    """Guard. The one exception to the title rule: the least destructive answer comes first."""
    page, _, _ = open_dialog(open_app, "confirm-over-detail")

    assert eventually(page, lambda: focused(page) == "#confirmCancelBtn", 1.0), f"focus is on {focused(page)}"


# ── Tab stays inside ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["composer", "detail", "onboarding", "picker", "day", "share"])
def test_tab_never_leaves_an_open_dialog(open_app, name):
    """aria-modal was declared on every dialog, but nothing kept focus inside."""
    page, dialog, _ = open_dialog(open_app, name)
    # Some dialogs focus from a short timer. Tabbing before it fires restarts
    # the walk halfway; let it settle, then go round the dialog twice.
    page.wait_for_timeout(200)

    escaped = set()
    for _ in range(60):
        page.keyboard.press("Tab")
        if not focus_is_inside(page, dialog):
            escaped.add(focused(page))

    assert not escaped, f"Tab left the {name} dialog and reached: {sorted(escaped)}"


# ── Escape, and only the dialog on top ───────────────────────────────────────

@pytest.mark.parametrize("name", ["onboarding", "picker", "day", "share"])
def test_escape_closes_the_dialog(open_app, name):
    """The picker already closed on Escape (guard); onboarding and the day sheet did not."""
    page, dialog, _ = open_dialog(open_app, name)
    page.wait_for_timeout(200)
    page.keyboard.press("Escape")

    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)


@pytest.mark.parametrize("close_with", ["escape", "telegram-back"])
@pytest.mark.parametrize("stack", ["picker-over-composer", "confirm-over-detail", "share-over-detail"])
def test_closing_the_top_dialog_leaves_the_one_below_open(open_app, stack, close_with):
    """Only the topmost dialog closes.

    Three of the four cases already worked (guards). The fourth is the gap
    Step 1 left: Telegram's back button closed the detail page underneath an
    open confirm and left the confirm floating over nothing.
    """
    if stack == "picker-over-composer":
        top, below = "picker", "#composerSheet"
    elif stack == "share-over-detail":
        top, below = "share", "#detailSheet"
    else:
        top, below = "confirm-over-detail", "#detailSheet"
    page, dialog, _ = open_dialog(open_app, top)
    page.wait_for_timeout(200)
    if close_with == "escape":
        page.keyboard.press("Escape")
    else:
        press_telegram_back(page)

    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)
    expect(page.locator(below)).to_be_visible()


# ── Focus back to the opener ─────────────────────────────────────────────────

def test_closing_the_picker_returns_focus_to_its_field(open_app):
    """Guard."""
    page, dialog, _ = open_dialog(open_app, "picker")
    page.click("#dpCancel")
    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)

    assert eventually(page, lambda: focused(page) == "#date", 1.0), f"focus is on {focused(page)}"


def test_closing_the_day_sheet_returns_focus_to_its_day(open_app):
    """The day sheet never gave focus back: after closing, it was on nothing."""
    page, dialog, _ = open_dialog(open_app, "day")
    page.wait_for_timeout(200)
    page.click("#dayClose")
    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)

    on_today = "() => document.activeElement.classList.contains('is-today')"
    assert eventually(page, lambda: page.evaluate(on_today), 1.0), f"focus is on {focused(page)}"


def test_a_stacked_dialog_keeps_the_focus_return_of_the_one_below(open_app):
    """The composer and the picker shared one "last focused" slot.

    Opening the picker overwrote the composer's, so closing the composer
    afterwards sent focus to the hidden date field instead of the Add button.
    """
    page, _, _ = open_dialog(open_app, "picker")
    page.click("#dpCancel")
    expect(page.locator("#dpOverlay")).to_be_hidden(timeout=FAST)
    page.keyboard.press("Escape")
    expect(page.locator("#composerSheet")).to_be_hidden(timeout=FAST)

    assert eventually(page, lambda: focused(page) == "#openComposerBtn", 1.0), f"focus is on {focused(page)}"


# ── Guards for the flows the migration rewrites ──────────────────────────────

def test_the_date_picker_opens_on_the_fields_date(open_app):
    """Guard for a Batch 13 fix. The wheels have no scroll box until the picker
    is shown, so filling it before showing it parked every wheel on
    1900 / January / 1. Checked by what sits in the middle of the wheel, not by
    aria-selected, which was right even while the wheel showed 1900."""
    page, _, _ = open_dialog(open_app, "picker")
    centred_year = """() => { const w = document.getElementById('dpYear');
        const middle = w.scrollTop + w.clientHeight / 2;
        let best = null, distance = Infinity;
        for (const item of w.querySelectorAll('.dp-item')) {
            const d = Math.abs(item.offsetTop - w.offsetTop + item.offsetHeight / 2 - middle);
            if (d < distance) { distance = d; best = item; } }
        return best ? best.textContent.trim() : null; }"""

    year = str(date.today().year)
    assert eventually(page, lambda: page.evaluate(centred_year) == year, 1.5), (
        f"the year wheel is centred on {page.evaluate(centred_year)}, not {year}"
    )


def test_an_event_opened_from_the_day_sheet_shows_its_detail(open_app):
    """Guard. The day sheet hands over to the detail page; the migration changes how it closes."""
    page, dialog, _ = open_dialog(open_app, "day")
    page.click(".day-item >> nth=0")

    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)
    expect(page.locator("#detailSheet")).to_be_visible(timeout=FAST)
    expect(page.locator("#detailEventTitle")).to_have_text("Dentist appointment")


# ── The share sheet, and what must not change about it ───────────────────────

def test_share_opens_over_the_detail_page_and_returns_to_it(open_app):
    """Guard for what must stay as it is: sharing opens on top of the detail
    page, and closing it lands back on the same event, not on the list."""
    page, dialog, _ = open_dialog(open_app, "share")
    expect(page.locator("#detailSheet")).to_be_visible()

    page.click("#shrClose")
    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)
    expect(page.locator("#detailSheet")).to_be_visible()
    expect(page.locator("#detailEventTitle")).to_have_text("Mom's birthday")


def test_closing_share_returns_focus_to_the_share_button(open_app):
    """The share sheet never gave focus back: after closing, it was on nothing."""
    page, dialog, _ = open_dialog(open_app, "share")
    page.wait_for_timeout(200)
    page.click("#shrClose")
    expect(page.locator(dialog)).to_be_hidden(timeout=FAST)

    assert eventually(page, lambda: focused(page) == "#detailShareBtn", 1.0), f"focus is on {focused(page)}"


def test_turning_the_public_link_back_on_still_asks_first(open_app):
    """Guard. Opening the sheet turns the public link on; switching it off and
    on again asks first, in a box of its own. That question has to stay
    reachable once the sheet is a dialog in the top layer."""
    page, _, _ = open_dialog(open_app, "share")
    switch = page.locator("#shrSwitch")
    expect(switch).to_have_attribute("aria-checked", "true", timeout=8000)

    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false", timeout=5000)
    switch.click()
    page.click(".shr-confirm-card [data-answer=yes]")

    expect(switch).to_have_attribute("aria-checked", "true", timeout=5000)


def test_escape_answers_the_public_link_question_and_leaves_share_open(open_app):
    """The question is the layer on top, so Escape answers it with no.

    Before Batch 20 Escape reached past both and closed the detail page
    underneath, leaving the question and the sheet standing.
    """
    page, dialog, _ = open_dialog(open_app, "share")
    switch = page.locator("#shrSwitch")
    expect(switch).to_have_attribute("aria-checked", "true", timeout=8000)
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false", timeout=5000)
    switch.click()
    expect(page.locator(".shr-confirm-card")).to_be_visible()

    page.keyboard.press("Escape")

    expect(page.locator(".shr-confirm")).to_have_count(0)
    expect(page.locator(dialog)).to_be_visible()
    expect(switch).to_have_attribute("aria-checked", "false")


def test_a_message_stays_announced_while_a_dialog_is_open(open_app):
    """Decision A of Batch 20: the one status region follows the dialog on top.

    Everything outside an open modal dialog is inert, and an inert live region
    is neither shown above the dialog nor announced. Measured in Chromium: the
    region was dropped from the accessibility tree, so a screen reader never
    heard "Please enter an event title." while the composer was open.
    """
    page, _, _ = open_dialog(open_app, "picker")
    page.evaluate("""() => { const region = document.getElementById('toast');
        region.textContent = 'Please enter an event title.';
        region.classList.add('is-visible'); }""")
    page.wait_for_timeout(200)

    assert not ax_ignored(page, "#toast"), "the status region is hidden from the accessibility tree"
    expect(page.locator("#toast")).to_be_visible()
