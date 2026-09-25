"""One test per runtime finding from the Batch 20 accessibility review.

Every test here is expected to FAIL against main at cdeddfb. That is the point:
a fix is only proven when a test that failed before it starts passing after
it. None of them may be skipped, xfailed or loosened to get the branch green;
CONSTRAINTS.md says so, and REQUIREMENTS in docs/a11y/ names what each pins.
The Critical A11Y-02 tests are in test_a11y_destructive_actions.py, which
ships to main as a hotfix.
The A11Y-01 dialog tests are in test_a11y_dialogs.py.

Run only these:          pytest -m browser
Leave them out locally:  pytest -m "not browser"
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

FAST = 1500  # ms. Every expectation below is met within a frame or two, or not at all.


def focused(page) -> str:
    return page.evaluate("""() => { const a = document.activeElement;
        if (!a) return 'nothing';
        const cls = a.className ? '.' + String(a.className).split(' ')[0] : '';
        return a.id ? '#' + a.id : a.tagName.toLowerCase() + cls; }""")


def activate_row_delete(page, title: str) -> None:
    """Presses a row's Delete exactly as the end of a swipe-and-tap does."""
    page.evaluate("""(title) => [...document.querySelectorAll('.event-row .row-action-delete')]
        .find((b) => b.getAttribute('aria-label').endsWith(title)).click()""", title)


def describe_violations(violations: list[dict]) -> str:
    lines = []
    for violation in violations:
        lines.append(f"{violation['id']} ({violation['impact']}): {violation['help']}")
        for node in violation["nodes"][:3]:
            summary = " ".join((node.get("failureSummary") or "").split())
            lines.append(f"    {node['target'][0]} -> {summary[:160]}")
    return "\n".join(lines)


def open_dialog(open_app, name: str):
    """Opens one of the app's dialogs the way a keyboard or pointer user would."""
    if name == "onboarding":
        page = open_app(onboarding_seen=False)
        selector = "#onboardingOverlay"
    else:
        page = open_app()
        if name == "detail":
            page.focus(".event-card >> nth=0")
            page.keyboard.press("Enter")
            selector = "#detailSheet"
        elif name == "confirm":
            activate_row_delete(page, "Tax return")
            selector = "#confirmOverlay"
        elif name == "day":
            page.click("[data-view=month]")
            page.focus(".cal-day.is-today")
            page.keyboard.press("Enter")
            selector = ".day-overlay"
        elif name == "composer":
            page.click("#openComposerBtn")
            selector = "#composerSheet"
        else:
            raise AssertionError(f"unknown dialog {name}")
    expect(page.locator(selector)).to_be_visible()
    return page, selector


# ── Keyboard access ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["Enter", "Space"])
@pytest.mark.parametrize("field", ["date", "date-jalali", "repeatUntil"])
def test_the_date_field_opens_the_picker_from_the_keyboard(open_app, field, key):
    """A11Y-08 / 2.1.1. The date fields are readonly and only a click opened the picker.

    Without it a keyboard-only user cannot give an event a date, which means
    they cannot create an event at all. Repeat-until is the third such field.
    """
    page, _ = open_dialog(open_app, "composer")
    if field == "repeatUntil":
        page.select_option("#repeat", "daily")
        expect(page.locator("#repeatUntil")).to_be_visible()
    page.focus(f"#{field}")
    page.keyboard.press(key)

    expect(page.locator("#dpOverlay")).to_be_visible(timeout=FAST)


def tab_to(page, selector: str, limit: int = 40) -> None:
    """Presses Tab, and only Tab, until the element has focus."""
    for _ in range(limit):
        if page.evaluate("(s) => document.activeElement === document.querySelector(s)", selector):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"{selector} was never reached with Tab; focus is on {focused(page)}")


def test_a_keyboard_only_user_can_date_and_save_an_event(open_app):
    """A11Y-08's reason to exist, end to end and with the keyboard alone: open
    the composer, type a title, give it a date in the picker, save."""
    page = open_app()
    tab_to(page, "#openComposerBtn")
    page.keyboard.press("Enter")
    expect(page.locator("#composerSheet")).to_be_visible()
    tab_to(page, "#title")
    page.keyboard.type("Physio session")
    tab_to(page, "#date")
    page.keyboard.press("Enter")
    expect(page.locator("#dpOverlay")).to_be_visible(timeout=FAST)
    tab_to(page, "#dpConfirm")
    page.keyboard.press("Enter")
    expect(page.locator("#dpOverlay")).to_be_hidden(timeout=FAST)
    expect(page.locator("#date")).not_to_have_value("")
    tab_to(page, "#saveEventBtn")
    page.keyboard.press("Enter")

    expect(page.locator("#composerSheet")).to_be_hidden(timeout=5000)
    expect(page.locator(".event-title", has_text="Physio session")).to_have_count(1, timeout=5000)


def test_the_skip_link_becomes_visible_when_focused(open_app):
    """A11Y-03 / 2.4.7. .sr-only has no :focus rule, so the first Tab stop is a 1x1 px link."""
    page = open_app()
    page.keyboard.press("Tab")
    link = page.locator("a[href='#eventList']")
    expect(link).to_be_focused()

    box = link.bounding_box()
    assert box["width"] >= 40 and box["height"] >= 20, (
        f"the focused skip link renders at {box['width']:.0f}x{box['height']:.0f} px"
    )


# ── Name, role, state ────────────────────────────────────────────────────────

def test_filter_buttons_expose_which_filter_is_active(open_app):
    """A11Y-03 / 4.1.2. The active filter is a CSS class and nothing else."""
    page = open_app()
    page.click("[data-filter=work]")

    expect(page.locator("[data-filter=work]")).to_have_attribute("aria-pressed", "true", timeout=FAST)
    expect(page.locator("[data-filter=all]")).to_have_attribute("aria-pressed", "false", timeout=FAST)


def test_calendar_days_are_named_with_their_date(open_app):
    """A11Y-03 / 1.3.1. Each day is announced as a bare number ("16"), today included.

    The month and the year live in a separate heading, the event dots are
    purely visual, and nothing but a colour says which cell is today.
    """
    page = open_app()
    page.click("[data-view=month]")
    today = page.locator(".cal-day.is-today")
    expect(today).to_be_visible()
    month_and_year = page.text_content(".cal-title").split()
    snapshot = today.aria_snapshot()

    assert all(part in snapshot for part in month_and_year), f"today is announced as {snapshot!r}"
    expect(today).to_have_attribute("aria-current", "date", timeout=FAST)


def test_list_sections_are_headings(open_app):
    """A11Y-04 / 1.3.1. 'Pinned', 'Today', 'Later' are divs with role=presentation."""
    page = open_app()

    expect(page.get_by_role("heading", name="Pinned", exact=True)).to_have_count(1, timeout=FAST)


def test_event_titles_are_not_flattened_inside_a_button(open_app):
    """A11Y-04 / 1.3.1. Each card is <article role="button"> with an aria-label.

    A button's children are presentational, so the h3 inside is no heading to
    a screen reader, and the aria-label replaces everything else on the card:
    the countdown, the date, the reminder time and the status are never read.
    (Playwright's aria snapshot still lists the children, which is why this
    checks the structure rather than the snapshot.)
    """
    page = open_app()
    flattened = page.locator(
        "[role=button] :is(h1,h2,h3,h4,h5,h6,[role=heading]), button :is(h1,h2,h3,h4,h5,h6,[role=heading])"
    )

    assert flattened.count() == 0, f"{flattened.count()} event title(s) sit inside a button"


def ax_node(page, selector: str) -> dict:
    """Role, name and description of the first match, as Chromium hands them to
    a screen reader (not as the markup spells them)."""
    cdp = page.context.new_cdp_session(page)
    document = cdp.send("DOM.getDocument", {"depth": 0})
    node = cdp.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
    tree = cdp.send("Accessibility.getPartialAXTree", {"nodeId": node["nodeId"], "fetchRelatives": False})
    ax = tree["nodes"][0]
    return {key: (ax.get(key) or {}).get("value", "") for key in ("role", "name", "description")}


def test_an_event_card_is_announced_with_its_countdown_and_date(open_app):
    """A11Y-04, decided in Batch 20 (option D, measured at 0 pixels changed): the
    card stays one button, named by its title and described by its own badges,
    dates and status.

    Chromium does keep the h3 and the dates of a role="button" in its tree, but
    a phone screen reader usually reads a button as one stop by its name, and
    that name was "Open details for ...". The description is what carries the
    countdown and the date to every screen reader alike.
    """
    page = open_app()
    card = page.locator(".event-card >> nth=0")
    iso = card.locator("[data-f=iso]").text_content()
    countdown = card.locator("[data-f=urgency]").text_content()
    node = ax_node(page, ".event-card")

    assert node["role"] == "button" and node["name"] == "Mom's birthday", node
    assert iso in node["description"] and countdown in node["description"], node


def test_filtering_and_searching_announce_how_many_events_are_shown(open_app):
    """A11Y-04, decided in Batch 20: the list is no live region any more, so a
    short status of its own says how many events a filter or a search leaves.
    Loading the list, or reloading it after a save, stays quiet."""
    page = open_app()
    status = page.locator("#listStatus")
    expect(status).to_have_attribute("role", "status")
    expect(status).to_have_text("")

    page.click("[data-filter=birthday]")
    expect(status).to_have_text("1 event", timeout=FAST)
    page.click("[data-filter=all]")
    expect(status).to_have_text("3 events", timeout=FAST)
    page.fill("#searchInput", "zzz")
    expect(status).to_have_text("No events found", timeout=3000)


def test_a_missing_title_is_reported_on_the_field_itself(open_app):
    """A11Y-06 / 3.3.1. The only error is a toast that disappears after 2.8 s."""
    page, _ = open_dialog(open_app, "composer")
    page.click("#saveEventBtn")
    title = page.locator("#title")

    expect(title).to_have_attribute("aria-invalid", "true", timeout=FAST)
    ids = (title.get_attribute("aria-describedby") or "").split()
    message = " ".join(page.locator(f"#{i}").inner_text() for i in ids).strip()
    assert message, "no error message is connected to the field through aria-describedby"


def test_a_field_error_stays_until_the_field_changes(open_app):
    """A11Y-06 / 3.3.1, decided in Batch 20: the message is visible under its
    field and stays there. The old toast was gone after 2.8 s; this one leaves
    only when the user edits the field."""
    page, _ = open_dialog(open_app, "composer")
    page.click("#saveEventBtn")
    error = page.locator("#titleError")
    expect(error).to_be_visible(timeout=FAST)
    page.wait_for_timeout(3200)

    expect(error).to_be_visible()
    expect(error).not_to_have_text("")
    page.fill("#title", "Dentist")
    expect(error).to_be_hidden(timeout=FAST)
    expect(page.locator("#title")).not_to_have_attribute("aria-invalid", "true")


@pytest.mark.parametrize("field", ["date", "eventTime"])
def test_the_other_required_fields_report_their_error_the_same_way(open_app, field):
    """A11Y-06: the date, and the time of an event that is not all-day, are
    checked, marked and described the same way as the title."""
    page, _ = open_dialog(open_app, "composer")
    page.fill("#title", "Team lunch")
    if field == "eventTime":
        page.click("#date")
        page.click("#dpConfirm")
        expect(page.locator("#dpOverlay")).to_be_hidden(timeout=FAST)
        page.uncheck("#allDay")
        page.evaluate("() => { document.getElementById('eventTime').value = ''; }")
    page.click("#saveEventBtn")

    expect(page.locator(f"#{field}")).to_have_attribute("aria-invalid", "true", timeout=FAST)
    expect(page.locator(f"#{field}Error")).to_be_visible()
    expect(page.locator(f"#{field}")).to_have_attribute("aria-describedby", f"{field}Error")


# ── External opinion: axe-core ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "state",
    ["list-light", "list-dark", "composer", "detail", "month", "composer-dark", "detail-dark", "month-dark"],
)
def test_axe_finds_no_serious_or_critical_violations(open_app, axe, state):
    """A11Y-05. Measured in the review: muted text 2.8-3.1:1, brand text 4.24:1,
    dark muted text 2.74:1, and a focusable year strip inside aria-hidden.
    """
    page = open_app(scheme="dark" if state.endswith("-dark") else "light")
    if state.startswith("composer"):
        page.click("#openComposerBtn")
    elif state.startswith("detail"):
        page.click(".event-card >> nth=0")
    elif state.startswith("month"):
        page.click("[data-view=month]")
        page.wait_for_selector(".cal-day")
    page.wait_for_timeout(300)

    violations = axe(page)
    assert not violations, describe_violations(violations)


STRESS_THEMES = {
    "light": {
        "bg_color": "#ffffff", "secondary_bg_color": "#ffffff", "section_bg_color": "#ffffff",
        "text_color": "#000000", "subtitle_text_color": "#999999", "hint_color": "#999999",
        "link_color": "#2481cc",
    },
    # Batch 20: the same guard has to work the other way round, lightening a
    # hint that is too dark for a dark theme.
    "dark": {
        "bg_color": "#212121", "secondary_bg_color": "#181818", "section_bg_color": "#212121",
        "text_color": "#ffffff", "subtitle_text_color": "#5e5e5e", "hint_color": "#5e5e5e",
        "link_color": "#1c5a8f",
    },
}


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_muted_text_stays_readable_under_a_low_contrast_telegram_theme(open_app, axe, scheme):
    """A11Y-05 / decision D4. A synthetic stress theme, not a real client.

    Telegram hands the app its hint colour; #999999 on white is 2.85:1. The app
    cannot choose the user's theme, so it has to correct a colour that fails
    rather than pass it through.
    """
    page = open_app(scheme=scheme, theme=STRESS_THEMES[scheme])

    violations = [v for v in axe(page) if v["id"] == "color-contrast"]
    assert not violations, describe_violations(violations)


def test_primary_controls_are_at_least_44_css_pixels(open_app):
    """A11Y-03 / decision D3: primary controls answer to a 44x44 px touch area.

    Decided in Batch 20: the visible size stays, and an invisible margin around
    each control makes up the difference. So this measures what a finger meets,
    not the painted box: the edges and corners of a 44x44 square centred on the
    control must all land on the control itself (2.5.5; 24x24 is the AA floor).
    Measured in the review: refresh and invite buttons 42x42, filter chips 39 px tall.
    """
    page = open_app()
    too_small = page.evaluate("""(selectors) => selectors
        .flatMap((s) => [...document.querySelectorAll(s)])
        .filter((e) => e.checkVisibility())
        .filter((e) => {
            e.scrollIntoView({ block: 'center', inline: 'center' });
            const r = e.getBoundingClientRect();
            const x = r.left + r.width / 2, y = r.top + r.height / 2, reach = 21.5;
            return [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]]
                .some(([dx, dy]) => {
                    const hit = document.elementFromPoint(x + dx * reach, y + dy * reach);
                    return !(hit && (hit === e || e.contains(hit)));
                });
        })
        .map((e) => e.id || e.dataset.filter || e.dataset.view)""",
        ["#refreshBtn", "#refOpenBtn", "#openComposerBtn", ".seg-btn", ".tab"])

    assert not too_small, f"controls whose 44x44 touch area misses them: {too_small}"


def test_the_note_and_checklist_tabs_work_like_tabs(open_app):
    """A11Y-03 / 4.1.2, decided in Batch 20: note and checklist are two panels,
    so they get the whole tab pattern: each tab controls its panel, only the
    selected tab is in the Tab order, and the arrow keys move between tabs."""
    page = open_app()
    page.focus(".event-card >> nth=0")
    page.keyboard.press("Enter")
    expect(page.locator("#detailSheet")).to_be_visible()

    note_tab = page.locator(".pane-tab[data-pane=note]")
    checklist_tab = page.locator(".pane-tab[data-pane=checklist]")
    expect(note_tab).to_have_attribute("tabindex", "0", timeout=FAST)
    expect(checklist_tab).to_have_attribute("tabindex", "-1")

    note_tab.focus()
    page.keyboard.press("ArrowRight")

    expect(checklist_tab).to_be_focused(timeout=FAST)
    expect(checklist_tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("#checklistPane")).to_be_visible()
    expect(page.locator("#detailNote")).to_be_hidden()


def test_the_calendar_switch_says_which_calendar_is_shown(open_app):
    """A11Y-03 / 4.1.2, decided in Batch 20: Gregorian/Jalali switches the same
    wheels, with no panel of its own, so it is a pair of toggle buttons."""
    page, _ = open_dialog(open_app, "composer")
    page.click("#date")
    expect(page.locator("#dpOverlay")).to_be_visible()
    page.click(".dp-tab[data-calendar=jalali]")

    jalali = page.locator(".dp-tab[data-calendar=jalali]")
    gregorian = page.locator(".dp-tab[data-calendar=gregorian]")
    expect(jalali).to_have_attribute("aria-pressed", "true", timeout=FAST)
    expect(gregorian).to_have_attribute("aria-pressed", "false")


def test_the_year_strip_is_not_a_trap_for_the_keyboard(open_app):
    """A11Y-05 / 2.1.1, decided in Batch 20 (Y1). The strip is a button per day
    of the year inside aria-hidden: 365 Tab stops a screen reader never names.
    It keeps its look and its click; the month grid, named day by day since
    A11Y-03, is the keyboard way to the same day."""
    page = open_app()
    page.click("[data-view=month]")
    page.wait_for_selector("#yearStrip .px")
    in_tab_order = page.evaluate(
        "() => [...document.querySelectorAll('#yearStrip .px')].filter((e) => e.tabIndex >= 0).length")

    assert in_tab_order == 0, f"{in_tab_order} year-strip days are Tab stops"
    page.click("#yearStrip .px.is-today")
    expect(page.locator("#dayClose")).to_be_visible(timeout=FAST)


# ── Clean console under the production CSP ───────────────────────────────────

def test_opening_the_app_outside_telegram_raises_no_csp_violation(open_app):
    """A11Y-09. fatal() injects style="..." markup, which style-src 'self' refuses.

    The Batch 19 test only scans templates, so JS-built markup slipped past it.
    This is the page anyone opening /webapp in a normal browser sees.
    """
    page = open_app(telegram=False)
    expect(page.get_by_text("only works inside Telegram")).to_be_visible()
    page.wait_for_timeout(300)

    refused = [text for text in page.console_errors if "Content Security Policy" in text]
    assert not refused, refused[:2]
