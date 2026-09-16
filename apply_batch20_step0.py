#!/usr/bin/env python3
"""Batch 20, step 0: accessibility review scaffold. It adds FAILING tests on purpose.

What it changes, and nothing else:
  1. tests/browser/                   real-Chromium harness and 28 browser tests (red by design)
  2. tests/test_a11y_findings.py      7 fast tests: 6 red by design, 1 checks the contrast formula
  3. tests/browser/vendor/axe.min.js  axe-core 4.13.0 from registry.npmjs.org, SHA-256 verified
  4. docs/a11y/REQUIREMENTS.md and CONSTRAINTS.md
  5. requirements-dev.txt (+ playwright), pyproject.toml (browser marker),
     .github/workflows/ci.yml (Chromium step)

Run from the repository root:  python apply_batch20_step0.py
Running it twice is safe: the second run reports every step as already done.
Every anchor is checked before anything is written, so it never half-applies.
Expected right after applying, on top of cdeddfb: 34 failed, 268 passed.
"""
from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path.cwd()
AXE_URL = "https://registry.npmjs.org/axe-core/-/axe-core-4.13.0.tgz"
AXE_MEMBER = "package/axe.min.js"
AXE_TARGET = "tests/browser/vendor/axe.min.js"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"

FILES: dict[str, str] = {}

FILES['tests/browser/__init__.py'] = r''''''

FILES['tests/browser/conftest.py'] = r'''"""Real-browser harness for the Batch 20 accessibility findings.

The app under test is the real one: templates, static files, security headers
and the Content-Security-Policy all come from app.main, served by uvicorn on a
free localhost port. Exactly two things are replaced:

* MongoDB, by the same in-memory mongomock the rest of the suite uses;
* Telegram's SDK script, by a stub defining window.Telegram.WebApp, because
  telegram.org is an external service. The initData it carries is signed with
  the test bot token, so the real authentication path accepts it.

Every page opened through `open_app` gets its own Telegram user, so tests
share neither data nor rate-limit budgets.

axe-core is vendored in tests/browser/vendor and its SHA-256 is checked before
it is injected: an outside WCAG opinion is only worth something if it is the
exact one that was reviewed.

A page opened inside Telegram must not log a page error or a CSP violation.
That check runs when each test finishes, so it guards every test here.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import socket
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
import uvicorn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.services.auth import compute_telegram_hash
from tests.harness import install_fake_db, seed_event, teardown_fake_db, utc

TELEGRAM_SDK = "https://telegram.org/js/telegram-web-app.js"
ONBOARDING_SEEN = "try { localStorage.setItem('tmp_onboarding_seen_v1', '1'); } catch (e) {}"

AXE_PATH = Path(__file__).parent / "vendor" / "axe.min.js"
AXE_VERSION = "4.13.0"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]

_users = itertools.count(700001)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveServer:
    """app.main on a background thread, with an event loop tests can reach."""

    def __init__(self) -> None:
        from app.main import app

        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.loop = asyncio.new_event_loop()
        # lifespan off: startup would call Telegram and a real MongoDB.
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, lifespan="off", log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._serve, name="a11y-live-server", daemon=True)

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.serve())

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the live server did not start")
            time.sleep(0.05)

    def run(self, coroutine):
        """Runs a coroutine on the server's own loop, so Mongo sees one loop."""
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=15)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="module")
def live_server():
    install_fake_db()
    server = LiveServer()
    server.start()
    yield server
    server.stop()
    teardown_fake_db()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except PlaywrightError as exc:
            raise RuntimeError(
                "Chromium for Playwright is not installed. Run: python -m playwright install chromium"
            ) from exc
        yield chromium
        chromium.close()


def signed_init_data(user_id: int, lang: str) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": f"a11y{user_id}",
        "user": json.dumps(
            {"id": user_id, "first_name": "Test", "language_code": lang}, separators=(",", ":")
        ),
    }
    fields["hash"] = compute_telegram_hash(fields, os.environ["BOT_TOKEN"])
    return urlencode(fields)


def telegram_stub(user_id: int, lang: str, scheme: str, theme: dict | None) -> str:
    """Only the surface static/*.js actually touches."""
    return f"""
window.Telegram = {{ WebApp: {{
  initData: {json.dumps(signed_init_data(user_id, lang))},
  initDataUnsafe: {{ user: {{ id: {user_id}, first_name: "Test", language_code: {json.dumps(lang)} }} }},
  colorScheme: {json.dumps(scheme)},
  themeParams: {json.dumps(theme or {})},
  version: "8.0", platform: "tdesktop",
  isVersionAtLeast: function () {{ return true; }},
  ready: function () {{}}, expand: function () {{}}, close: function () {{}},
  onEvent: function () {{}}, offEvent: function () {{}},
  setHeaderColor: function () {{}}, setBackgroundColor: function () {{}},
  openTelegramLink: function () {{}}, openLink: function () {{}},
  shareMessage: function (id, done) {{ if (done) done(true); }},
  HapticFeedback: {{ impactOccurred: function () {{}}, notificationOccurred: function () {{}},
                     selectionChanged: function () {{}} }},
  BackButton: {{ show: function () {{}}, hide: function () {{}}, onClick: function () {{}},
                 offClick: function () {{}} }},
  MainButton: {{ show: function () {{}}, hide: function () {{}}, setText: function () {{}},
                 onClick: function () {{}}, offClick: function () {{}} }}
}} }};
"""


def default_events() -> list[dict]:
    """Three events: one today, one pinned, one far away. Titles are unique."""
    today = date.today()
    return [
        {"title": "Dentist appointment", "category": "health", "date_iso": today.isoformat(),
         "event_ts_utc": utc(hours=6)},
        {"title": "Mom's birthday", "category": "birthday", "pinned": True,
         "date_iso": (today + timedelta(days=3)).isoformat(), "event_ts_utc": utc(days=3)},
        {"title": "Tax return", "category": "finance",
         "date_iso": (today + timedelta(days=44)).isoformat(), "event_ts_utc": utc(days=44)},
    ]


@pytest.fixture
def open_app(browser, live_server):
    """Opens /webapp as a fresh Telegram user and returns the Playwright page.

    The page carries what the tests read back: `api_calls` (POST /api/* in
    order), `deleted` (event ids sent to /api/delete), `event_ids` (title -> id),
    `console_errors` and `page_errors`.
    """
    opened: list[tuple[object, bool]] = []

    def factory(*, lang="en", scheme="light", theme=None, width=390,
                onboarding_seen=True, telegram=True):
        user_id = next(_users)
        seeded = [live_server.run(seed_event(user_id=str(user_id), **event)) for event in default_events()]

        context = browser.new_context(
            viewport={"width": width, "height": 844}, color_scheme=scheme, reduced_motion="reduce",
        )
        context.set_default_timeout(5000)
        if onboarding_seen:
            context.add_init_script(ONBOARDING_SEEN)

        page = context.new_page()
        page.api_calls, page.deleted, page.console_errors, page.page_errors = [], [], [], []
        page.event_ids = {doc["title"]: str(doc["_id"]) for doc in seeded}

        def record(request):
            if request.method == "POST" and "/api/" in request.url:
                name = request.url.split("/api/", 1)[1]
                page.api_calls.append(name)
                if name == "delete":
                    page.deleted.append((request.post_data_json or {}).get("event_id"))

        page.on("request", record)
        page.on("console", lambda msg: msg.type == "error" and page.console_errors.append(msg.text))
        page.on("pageerror", lambda error: page.page_errors.append(str(error)))

        sdk = telegram_stub(user_id, lang, scheme, theme) if telegram else ""
        page.route(TELEGRAM_SDK, lambda route: route.fulfill(
            status=200, content_type="application/javascript", body=sdk))

        page.goto(live_server.url + "/webapp")
        if telegram:
            page.wait_for_selector(".event-card")
        opened.append((page, telegram))
        return page

    yield factory

    problems: list[str] = []
    for page, inside_telegram in opened:
        if inside_telegram:
            problems += [f"page error: {error}" for error in page.page_errors]
            problems += [f"CSP: {text}" for text in page.console_errors if "Content Security Policy" in text]
        page.context.close()
    assert not problems, "the page logged errors while the test ran:\n" + "\n".join(problems)


@pytest.fixture(scope="module")
def axe_source() -> str:
    data = AXE_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == AXE_SHA256, (
        f"{AXE_PATH.name} is not the reviewed axe-core {AXE_VERSION} (sha256 {digest}). "
        "Update vendor/README.md and AXE_SHA256 together, never one alone."
    )
    return data.decode("utf-8")


@pytest.fixture
def axe(axe_source):
    """Returns serious and critical WCAG 2.1 A/AA violations for the page as it is now."""

    def run(page) -> list[dict]:
        page.evaluate(axe_source)  # evaluate() is not subject to the page's CSP
        options = {"runOnly": {"type": "tag", "values": AXE_TAGS}, "resultTypes": ["violations"]}
        result = page.evaluate("async (options) => await axe.run(document, options)", options)
        return [v for v in result["violations"] if v["impact"] in ("serious", "critical")]

    return run

'''

FILES['tests/browser/test_a11y_browser.py'] = r'''"""One test per runtime finding from the Batch 20 accessibility review.

Every test here is expected to FAIL against main at cdeddfb. That is the point:
a fix is only proven when a test that failed before it starts passing after
it. None of them may be skipped, xfailed or loosened to get the branch green;
CONSTRAINTS.md says so, and REQUIREMENTS in docs/a11y/ names what each pins.

Run only these:          pytest -m browser
Leave them out locally:  pytest -m "not browser"
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

FAST = 1500  # ms. Every expectation below is met within a frame or two, or not at all.


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


# ── CRITICAL — data loss and actions that do nothing ─────────────────────────

def test_a_cancelled_delete_is_not_replayed_by_the_next_confirmation(open_app):
    """A11Y-02. Dismissing the confirm with Escape leaves its OK handler armed.

    showConfirm listens for Escape with {once: true}, so any earlier key (Tab)
    uses the listener up. The global Escape handler then hides the overlay
    without settling the promise, and the next OK click resolves it as well:
    deleting one event also deletes the one the user had just cancelled.
    Reproduced in Chromium during the review.
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
    """A11Y-02. A second activation stacks a second pending confirm.

    Focus never moves into the dialog, so the Delete that opened it can be
    pressed again. Each press adds another OK listener, and one OK then fires
    them all: the same event is deleted twice and the second call fails.
    """
    page = open_app()
    activate_row_delete(page, "Tax return")
    activate_row_delete(page, "Tax return")
    page.click("#confirmOkBtn")
    expect(page.locator(".event-title", has_text="Tax return")).to_have_count(0)
    page.wait_for_timeout(300)

    assert page.deleted == [page.event_ids["Tax return"]], f"DELETE requests sent: {len(page.deleted)}"


def test_the_detail_page_delete_button_asks_before_deleting(open_app):
    """A11Y-02 / 2.1.1. The button does nothing at all, for every user.

    addEventListener passes the click event as deleteCurrentEvent's first
    argument, so the `eventId = state.detailEventId` default never applies and
    getEventById(MouseEvent) returns null. Keyboard users have no other way to
    delete: the swipe actions are tabIndex -1 by design.
    """
    page, _ = open_dialog(open_app, "detail")
    page.click("#detailDeleteBtn")

    expect(page.locator("#confirmOverlay")).to_be_visible(timeout=FAST)


def test_the_detail_page_pin_button_sends_the_change(open_app):
    """A11Y-02 / 2.1.1. Same defect as Delete: toggleCurrentPin gets the click event."""
    page, _ = open_dialog(open_app, "detail")
    page.click("#detailPinBtn")

    assert eventually(page, lambda: "pin" in page.api_calls), (
        f"pressing Pin sent nothing; API calls after load: {page.api_calls}"
    )


# ── Keyboard access ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_the_date_field_opens_the_picker_from_the_keyboard(open_app, key):
    """A11Y-08 / 2.1.1. #date is readonly and only a click opens the picker.

    Without it a keyboard-only user cannot give an event a date, which means
    they cannot create an event at all.
    """
    page, _ = open_dialog(open_app, "composer")
    page.focus("#date")
    page.keyboard.press(key)

    expect(page.locator("#dpOverlay")).to_be_visible(timeout=FAST)


@pytest.mark.parametrize("name", ["detail", "confirm", "day", "onboarding"])
def test_opening_a_dialog_moves_focus_into_it(open_app, name):
    """A11Y-01 / 2.4.3. Focus stays on the control behind the modal.

    A screen reader therefore never announces the dialog, and the next Tab
    walks the page underneath it. The composer already does this right.
    """
    page, selector = open_dialog(open_app, name)

    assert eventually(page, lambda: focus_is_inside(page, selector), 1.0), (
        f"{name} opened but focus is on {focused(page)}"
    )


@pytest.mark.parametrize("name", ["composer", "detail"])
def test_tab_never_leaves_an_open_dialog(open_app, name):
    """A11Y-01 / 2.4.3. aria-modal is declared but nothing keeps focus inside."""
    page, selector = open_dialog(open_app, name)
    # Dialogs focus their first control from a 40 ms timer. Tabbing before it
    # fires restarts the walk halfway, which once let this pass on a leaking
    # composer. Let it settle, then press enough to go round the dialog twice.
    page.wait_for_timeout(200)

    escaped = set()
    for _ in range(60):
        page.keyboard.press("Tab")
        if not focus_is_inside(page, selector):
            escaped.add(focused(page))

    assert not escaped, f"Tab left the {name} dialog and reached: {sorted(escaped)}"


@pytest.mark.parametrize("name", ["day", "onboarding"])
def test_escape_closes_the_dialog(open_app, name):
    """A11Y-01. The global Escape handler knows the other dialogs but not these two."""
    page, selector = open_dialog(open_app, name)
    page.keyboard.press("Escape")

    expect(page.locator(selector)).to_be_hidden(timeout=FAST)


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


def test_a_missing_title_is_reported_on_the_field_itself(open_app):
    """A11Y-06 / 3.3.1. The only error is a toast that disappears after 2.8 s."""
    page, _ = open_dialog(open_app, "composer")
    page.click("#saveEventBtn")
    title = page.locator("#title")

    expect(title).to_have_attribute("aria-invalid", "true", timeout=FAST)
    ids = (title.get_attribute("aria-describedby") or "").split()
    message = " ".join(page.locator(f"#{i}").inner_text() for i in ids).strip()
    assert message, "no error message is connected to the field through aria-describedby"


# ── External opinion: axe-core ───────────────────────────────────────────────

@pytest.mark.parametrize("state", ["list-light", "list-dark", "composer", "detail", "month"])
def test_axe_finds_no_serious_or_critical_violations(open_app, axe, state):
    """A11Y-05. Measured in the review: muted text 2.8-3.1:1, brand text 4.24:1,
    dark muted text 2.74:1, and a focusable year strip inside aria-hidden.
    """
    page = open_app(scheme="dark" if state == "list-dark" else "light")
    if state == "composer":
        page.click("#openComposerBtn")
    elif state == "detail":
        page.click(".event-card >> nth=0")
    elif state == "month":
        page.click("[data-view=month]")
        page.wait_for_selector(".cal-day")
    page.wait_for_timeout(300)

    violations = axe(page)
    assert not violations, describe_violations(violations)


def test_muted_text_stays_readable_under_a_low_contrast_telegram_theme(open_app, axe):
    """A11Y-05 / decision D4. A synthetic stress theme, not a real client.

    Telegram hands the app its hint colour; #999999 on white is 2.85:1. The app
    cannot choose the user's theme, so it has to correct a colour that fails
    rather than pass it through.
    """
    stress_theme = {
        "bg_color": "#ffffff", "secondary_bg_color": "#ffffff", "section_bg_color": "#ffffff",
        "text_color": "#000000", "subtitle_text_color": "#999999", "hint_color": "#999999",
        "link_color": "#2481cc",
    }
    page = open_app(theme=stress_theme)

    violations = [v for v in axe(page) if v["id"] == "color-contrast"]
    assert not violations, describe_violations(violations)


def test_primary_controls_are_at_least_44_css_pixels(open_app):
    """A11Y-03 / decision D3: 44x44 for primary controls (2.5.5, AAA; 24x24 is the AA floor).

    Measured in the review: refresh and invite buttons 42x42, filter chips 39 px tall.
    """
    page = open_app()
    too_small = page.evaluate("""(selectors) => selectors
        .flatMap((s) => [...document.querySelectorAll(s)])
        .filter((e) => e.checkVisibility())
        .map((e) => { const r = e.getBoundingClientRect();
            return [e.id || e.dataset.filter || e.dataset.view, Math.round(r.width), Math.round(r.height)]; })
        .filter(([, w, h]) => w < 44 || h < 44)""",
        ["#refreshBtn", "#refOpenBtn", "#openComposerBtn", ".seg-btn", ".tab"])

    assert not too_small, f"[control, width, height] below 44 px: {too_small}"


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
'''

FILES['tests/test_a11y_findings.py'] = r'''"""One test per accessibility finding that needs no browser (Batch 20 review).

Every finding test here is expected to FAIL against main at cdeddfb; the
single exception checks the contrast formula itself against WCAG's reference
values, so the palette test cannot pass or fail on a wrong formula. These are the
fast half of the accessibility bar: they read what the templates and the
stylesheet declare, or render the public page on the server, in milliseconds.
What only a running page can show lives in tests/browser/.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.harness import fake_request, install_fake_db, seed_event, teardown_fake_db, utc

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
STYLESHEET = ROOT / "static" / "style.css"
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
}


class Node:
    def __init__(self, tag: str, attrs: list, parent: "Node | None"):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []


class TreeBuilder(HTMLParser):
    """Just enough of a DOM for the checks below; the standard library only."""

    def __init__(self):
        super().__init__()
        self.root = Node("#document", [], None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse(html: str) -> list[Node]:
    builder = TreeBuilder()
    builder.feed(html)
    nodes, stack = [], [builder.root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(child for child in reversed(node.children) if isinstance(child, Node))
    return nodes


def text_of(node) -> str:
    """Text a screen reader would get: aria-hidden subtrees contribute nothing."""
    if isinstance(node, str):
        return node
    if node.attrs.get("aria-hidden") == "true":
        return ""
    return "".join(text_of(child) for child in node.children)


# ── Template semantics ───────────────────────────────────────────────────────

def test_every_form_field_has_a_label_a_screen_reader_can_read():
    """A11Y-03 / 3.3.2. A placeholder is not a label: it disappears as you type.

    #searchInput has a <label for> whose only content is an aria-hidden icon;
    the detail note and the checklist input have no label at all.
    """
    nodes = parse(INDEX.read_text(encoding="utf-8"))
    label_text: dict[str, str] = {}
    for node in nodes:
        if node.tag == "label" and node.attrs.get("for"):
            label_text[node.attrs["for"]] = label_text.get(node.attrs["for"], "") + text_of(node)

    unlabeled = []
    for node in nodes:
        if node.tag not in ("input", "select", "textarea") or node.attrs.get("type") == "hidden":
            continue
        if (node.attrs.get("aria-label") or "").strip() or node.attrs.get("aria-labelledby"):
            continue
        if label_text.get(node.attrs.get("id", ""), "").strip():
            continue
        unlabeled.append(node.attrs.get("id", node.tag))

    assert not unlabeled, f"fields without a readable label: {unlabeled}"


def test_the_event_list_is_not_one_big_live_region():
    """A11Y-04 / 4.1.3. aria-live on #eventsWrap re-announces the whole list on every render.

    Status belongs in a short, dedicated region ("3 events, filter: Work").
    """
    nodes = parse(INDEX.read_text(encoding="utf-8"))
    wrap = next(node for node in nodes if node.attrs.get("id") == "eventsWrap")

    assert "aria-live" not in wrap.attrs and wrap.attrs.get("role") not in ("log", "status", "alert"), (
        f"#eventsWrap is a live region: {wrap.attrs}"
    )


def test_every_tab_controls_a_tab_panel():
    """A11Y-03 / 4.1.2. role=tab promises a tabpanel; neither tab set has one.

    The note/checklist tabs never say what they control, and the
    Gregorian/Jalali switch in the date picker is a toggle dressed as tabs.
    Either a real tab pattern or plain toggle buttons satisfies this test.
    """
    nodes = parse(INDEX.read_text(encoding="utf-8"))
    by_id = {node.attrs["id"]: node for node in nodes if "id" in node.attrs}

    broken = [
        node.attrs.get("data-pane") or node.attrs.get("data-calendar") or text_of(node).strip()
        for node in nodes
        if node.attrs.get("role") == "tab"
        and by_id.get(node.attrs.get("aria-controls", ""), Node("", [], None)).attrs.get("role") != "tabpanel"
    ]
    assert not broken, f"tabs without a tabpanel: {broken}"


# ── Colour contrast of the fallback palette ──────────────────────────────────

def _block(css: str, pattern: str) -> dict[str, str]:
    match = re.search(pattern, css, re.S | re.M)
    assert match, f"stylesheet block not found: {pattern}"
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", match.group(1)))


def _hex(tokens: dict[str, str], name: str) -> str:
    value = tokens[name].strip()
    for _ in range(5):  # var(--tg-x, #fallback) and var(--other) chains are short
        fallback = re.fullmatch(r"var\(--tg-[\w-]+,\s*(#[0-9a-fA-F]{6})\)", value)
        alias = re.fullmatch(r"var\(--([\w-]+)\)", value)
        if fallback:
            value = fallback.group(1)
        elif alias:
            value = tokens[alias.group(1)].strip()
        else:
            break
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"--{name} is not a plain colour: {value}"
    return value


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_ratio_matches_the_wcag_reference_values():
    """The measuring stick is checked before it measures anything."""
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_the_fallback_palette_meets_wcag_aa_text_contrast():
    """A11Y-05 / 1.4.3. The colours used when Telegram sends no theme.

    Measured by axe in the review and reproduced here without a browser, so the
    edit loop catches a regression in milliseconds: muted text 3.12:1 on white,
    2.8:1 on the page background; brand text 4.24:1; dark muted text 2.74:1.
    """
    css = STYLESHEET.read_text(encoding="utf-8")
    base = _block(css, r"^:root\s*\{(.*?)^\}")
    themes = {
        "light": base,
        "light [data-tg-scheme]": {**base, **_block(css, r'^:root\[data-tg-scheme="light"\]\s*\{(.*?)^\}')},
        "dark (media query)": {
            **base, **_block(css, r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}"),
        },
        "dark [data-tg-scheme]": {**base, **_block(css, r'^:root\[data-tg-scheme="dark"\]\s*\{(.*?)^\}')},
    }
    pairs = [
        ("text-muted", "surface", "labels and subtitles on cards and sheets"),
        ("text-muted", "bg", "the header subtitle"),
        ("brand", "surface", "the active tab and accent text"),
    ]

    failures = []
    for theme, tokens in themes.items():
        checks = pairs + ([("brand-text", "brand", "text on primary buttons and today")]
                          if theme.startswith("light") else [])
        for foreground, background, where in checks:
            ratio = contrast_ratio(_hex(tokens, foreground), _hex(tokens, background))
            if ratio < 4.5:
                failures.append(f"{theme}: --{foreground} on --{background} = {ratio:.2f}:1 ({where})")

    assert not failures, "below 4.5:1:\n" + "\n".join(failures)


# ── The public countdown page ────────────────────────────────────────────────

@pytest.fixture
def fake_db():
    install_fake_db()
    yield
    teardown_fake_db()


async def _render_countdown(**overrides) -> tuple[str, dict]:
    from app.routes.share import public_countdown
    from app.services.sharing import generate_public_token

    token = generate_public_token()
    event = await seed_event(public_token=token, public_enabled=True, title="Alice birthday", **overrides)
    response = await public_countdown(fake_request(f"/c/{token}"), token)
    return response.body.decode("utf-8"), event


async def test_the_countdown_image_alt_text_carries_the_countdown(fake_db):
    """A11Y-07 / 1.1.1. The card image shows how many days are left; its alt is only the title.

    The number of days is the whole point of the page, and a screen reader
    user never gets it.
    """
    from app.services.cards import days_until

    future = (datetime.now(timezone.utc) + timedelta(days=12)).date().isoformat()
    body, event = await _render_countdown(date_iso=future, event_ts_utc=utc(days=12))
    days = days_until(event)
    alt = next(node.attrs.get("alt", "") for node in parse(body) if node.tag == "img")

    assert str(days) in alt, f"alt is {alt!r}, but the image says {days} days"


async def test_the_countdown_page_has_a_main_landmark_and_a_heading(fake_db):
    """A11Y-07 / 1.3.1. The page is an image, two links and a footer, with no structure."""
    body, _ = await _render_countdown()
    tags = {node.tag for node in parse(body)}

    assert {"main", "h1"} <= tags, f"missing: {sorted({'main', 'h1'} - tags)}"
'''

FILES['tests/browser/vendor/README.md'] = r'''# Vendored: axe-core

- **Version:** 4.13.0
- **License:** MPL-2.0. This file is unmodified, and its license header is
  kept at the top of `axe.min.js`.
- **Source:** https://registry.npmjs.org/axe-core/-/axe-core-4.13.0.tgz
  (`package/axe.min.js`)
- **SHA-256:** `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`

It is vendored rather than installed so CI never needs npm. It is pinned by
checksum so the outside WCAG opinion is the exact one that was reviewed:
`tests/browser/conftest.py` refuses any other file.

## Updating

1. Download the new tarball and extract `package/axe.min.js` over this file.
2. Change `AXE_VERSION` and `AXE_SHA256` in `tests/browser/conftest.py`, and
   the two lines above, in the same commit.
3. Run `pytest -m browser`. A new axe version can find new violations. That is
   a finding to fix, not a reason to pin the old version.
'''

FILES['docs/a11y/REQUIREMENTS.md'] = r'''# Accessibility requirements — Batch 20

Standard: WCAG 2.1 AA, plus two project decisions (D3, D4 in CONSTRAINTS.md).
Every requirement names the tests that prove it. All of them were red on
`main` at `cdeddfb`, and every one was shown green against a throwaway fix in a
scratch copy, so none of them is a test that cannot pass.

| ID | Requirement | WCAG | Proven by |
|----|-------------|------|-----------|
| A11Y-01 | Every dialog moves focus inside when it opens, keeps Tab inside, closes on Escape and returns focus to its opener. Applies to the composer, detail page, date picker, confirm, onboarding and day sheet. | 2.1.2, 2.4.3 | `test_opening_a_dialog_moves_focus_into_it`, `test_tab_never_leaves_an_open_dialog`, `test_escape_closes_the_dialog` |
| A11Y-02 | Every way of dismissing a confirmation settles it. One confirmation sends exactly one request. Delete and Pin on the detail page work. | 2.1.1, correctness | `test_a_cancelled_delete_is_not_replayed_by_the_next_confirmation`, `test_one_confirmation_sends_exactly_one_delete`, `test_the_detail_page_delete_button_asks_before_deleting`, `test_the_detail_page_pin_button_sends_the_change` |
| A11Y-03 | Every control exposes name, role and state: fields have readable labels, filters expose `aria-pressed`, tabs control tab panels (or become toggles), calendar days are named with their date and today carries `aria-current="date"`, the skip link is visible on focus, and primary controls are at least 44×44 px. | 1.3.1, 2.4.7, 3.3.2, 4.1.2, 2.5.5 (AAA, D3) | `test_every_form_field_has_a_label_a_screen_reader_can_read`, `test_every_tab_controls_a_tab_panel`, `test_filter_buttons_expose_which_filter_is_active`, `test_calendar_days_are_named_with_their_date`, `test_the_skip_link_becomes_visible_when_focused`, `test_primary_controls_are_at_least_44_css_pixels` |
| A11Y-04 | The list has structure: section labels are headings, event titles are not flattened inside a button, and the list is not one live region. | 1.3.1, 4.1.3 | `test_list_sections_are_headings`, `test_event_titles_are_not_flattened_inside_a_button`, `test_the_event_list_is_not_one_big_live_region` |
| A11Y-05 | Text meets 4.5:1 in the fallback light and dark palettes. axe-core reports zero serious or critical violations on five states. A Telegram theme with a weak hint colour is corrected (D4). | 1.4.3, 1.4.11, 4.1.2 | `test_the_fallback_palette_meets_wcag_aa_text_contrast`, `test_axe_finds_no_serious_or_critical_violations`, `test_muted_text_stays_readable_under_a_low_contrast_telegram_theme` |
| A11Y-06 | A validation error is marked on its field (`aria-invalid`) and connected to a message (`aria-describedby`) that does not disappear. | 3.3.1 | `test_a_missing_title_is_reported_on_the_field_itself` |
| A11Y-07 | The public countdown page gives the countdown as text (in the image alt), and has a `main` landmark and an `h1`. | 1.1.1, 1.3.1 | `test_the_countdown_image_alt_text_carries_the_countdown`, `test_the_countdown_page_has_a_main_landmark_and_a_heading` |
| A11Y-08 | The date fields open the picker from the keyboard. | 2.1.1 | `test_the_date_field_opens_the_picker_from_the_keyboard[Enter/Space]` |
| A11Y-09 | No CSP violation in the console, inside Telegram (every browser test checks this at teardown) or outside it. | security, clean console | `test_opening_the_app_outside_telegram_raises_no_csp_violation` |

## Already met: keep it that way

These were verified in Chromium during the review:

- Persian RTL at 320 px has no horizontal scroll (1.4.10).
- `lang` and `dir` follow the Telegram language.
- The composer focuses its title and returns focus when it closes.
- The viewport allows zoom.

## Order of work (D2)

Step 1 is A11Y-02, the confirm lifecycle and the detail Delete/Pin buttons,
**in one commit**. Fixing the buttons alone would hand the replay bug to
keyboard users, who today cannot reach Delete at all. After that the order is
A11Y-01, 08, 03, 04, 06, 05, 07, 09. Each step makes its tests green and
leaves every other test as it was.

## What the reverse proof taught the real fixes

- **Date fields.** Act on Enter at keydown and call `stopPropagation`: the
  picker registers a document-level Enter listener during the same dispatch,
  and that listener confirms the picker at once. Act on Space at keyup, the
  way native buttons do. Do not autofocus Confirm on open: a 40 ms timer can
  hand the Space keyup to it. Run the Space test ten times before committing.
- **Day sheet.** Escape must work before focus has arrived, so use a
  document-level handler rather than one on the sheet.
- **Tabs to toggles.** Converting tabs to toggles also means removing
  `role="tablist"` and the `aria-selected` writes in JavaScript.
- **More contrast failures.** axe shows more nodes once the first are fixed.
  The Share action text (#059669 on #ecf9f5, 3.48:1) and the Delete action
  text (#ef4444 on #fef0f0, 3.39:1) also fail.

## Still manual (not automatable)

- VoiceOver in Telegram iOS and TalkBack in Telegram Android.
- A keyboard-only pass in Telegram Desktop.
- Contrast under a few real Telegram themes.

Record the results in the pull request.
'''

FILES['CONSTRAINTS.md'] = r'''# Constraints

Last reviewed: 2026-09-16 (Batch 20, step 0).

Scope today is the floor and accessibility. Coverage, security, performance
and observability rows belong to Batch 20 priority #2 and are added then. This
file is not weakened in the same commit as a change that was failing it.

## Floor (always)

- No skipped, xfailed or deleted tests to get green. A test that is wrong is
  fixed in its own commit, with the reason in the message.
- No threshold below is lowered in the same change that was failing it.
- No new ruff exclusions or `# noqa` for test files.
- Tightening this file is quiet. Loosening it is a reviewed change of its own.

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|-----------|------|------------|---------|
| Accessibility (external) | Zero serious or critical axe violations, tags `wcag2a wcag2aa wcag21a wcag21aa`, on list (light and dark), composer, detail and month | axe-core 4.13.0, vendored and SHA-256 pinned, in Chromium through Playwright: `pytest -m browser` | CI: every push to `main` and `fix/**`, and every PR |
| Accessibility (behaviour) | A11Y-01 to A11Y-09 in `docs/a11y/REQUIREMENTS.md` green | `pytest tests/browser tests/test_a11y_findings.py` | CI |
| Contrast (fast) | Fallback palette text pairs ≥ 4.5:1 | `pytest tests/test_a11y_findings.py` | Every local run (milliseconds) |
| Target size | Primary controls ≥ 44×44 CSS px (D3) | `test_primary_controls_are_at_least_44_css_pixels` | CI |
| Clean console | No page error and no CSP violation inside Telegram | teardown of the `open_app` fixture | CI |
| Lint | `ruff check .` clean | ruff 0.8.4 | CI |
| Production dependencies | No known vulnerabilities | `pip-audit --requirement requirements.txt` | CI |

The fast loop can leave the browser out with `pytest -m "not browser"`. CI
never does.

## Decisions

- **D1 — browser harness:** pytest, Playwright for Python, vendored
  axe-core. It stays in the project's language, uses a real browser, brings an
  outside WCAG opinion, and proves the CSP in the same run. The cost is one dev
  dependency and a Chromium download in CI.
- **D2 — order:** the Critical data-loss and dead-button findings come first
  (A11Y-02), in one commit.
- **D3 — target size:** 44×44 px for primary controls. That is WCAG 2.5.5
  (AAA) and the agent-skills checklist. The AA floor, 24×24 px (WCAG 2.2
  2.5.8), is already met everywhere measured.
- **D4 — Telegram themes:** the app corrects a hint or subtitle colour that
  falls below 4.5:1 against the theme background, instead of passing it
  through. The user's theme is not the app's to choose, but readable text is.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| — | none | | | | |
'''

# (path, marker that means already applied, mode, anchor, new text)
PATCHES = [
    ('pyproject.toml', 'markers = [', 'replace', 'python_functions = ["test_*"]\n', 'python_functions = ["test_*"]\n# Browser tests run by default; leave them out of a fast local loop with\n# pytest -m "not browser". CI always runs them.\nmarkers = ["browser: real Chromium through Playwright and axe-core (tests/browser)"]\n'),
    ('requirements-dev.txt', 'playwright==', 'replace', 'mongomock-motor==0.0.36\n', 'mongomock-motor==0.0.36\n# Real Chromium for the accessibility tests in tests/browser. After install:\n#   python -m playwright install chromium\n# axe-core itself is vendored in tests/browser/vendor, not installed.\nplaywright==1.56.0\n'),
    ('.github/workflows/ci.yml', 'playwright install', 'replace', '      - name: Audit production dependencies\n', '      - name: Install Chromium for the browser tests\n        # tests/browser runs the real app in Chromium with vendored axe-core.\n        run: python -m playwright install --with-deps chromium\n      - name: Audit production dependencies\n'),
]


def read_text(path: str) -> tuple[str, bool]:
    raw = (ROOT / path).read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def write_text(path: str, text: str, crlf: bool = False) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if not (ROOT / "app" / "main.py").exists() or not (ROOT / "tests" / "harness.py").exists():
        print("Run this from the time_manager_pro repository root.")
        return 1

    problems = []
    for path, content in FILES.items():
        target = ROOT / path
        if target.exists() and read_text(path)[0] != content:
            problems.append(f"{path} already exists with different content; resolve it by hand first")
    for path, marker, mode, anchor, _ in PATCHES:
        if not (ROOT / path).exists():
            problems.append(f"{path} is missing")
            continue
        text = read_text(path)[0]
        if marker not in text and mode == "replace" and text.count(anchor) != 1:
            problems.append(f"{path}: anchor not found exactly once: {anchor.strip()!r}")
    if problems:
        print("Nothing was changed:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1

    step = 0
    for path, content in FILES.items():
        step += 1
        if (ROOT / path).exists():
            print(f"Step {step:2d} already applied: {path}")
        else:
            write_text(path, content)
            print(f"Step {step:2d} OK: {path}")

    step += 1
    target = ROOT / AXE_TARGET
    if target.exists() and sha256(target.read_bytes()) == AXE_SHA256:
        print(f"Step {step:2d} already applied: {AXE_TARGET}")
    else:
        with urllib.request.urlopen(AXE_URL, timeout=60) as response:
            archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
        data = archive.extractfile(AXE_MEMBER).read()
        if sha256(data) != AXE_SHA256:
            print(f"Step {step:2d} FAILED: axe-core checksum mismatch ({sha256(data)}); nothing written for it.")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"Step {step:2d} OK: {AXE_TARGET} (sha256 verified)")

    for path, marker, mode, anchor, new in PATCHES:
        step += 1
        text, crlf = read_text(path)
        if marker in text:
            print(f"Step {step:2d} already applied: {path}")
            continue
        if mode == "append":
            text = text + ("" if text.endswith("\n") or not text else "\n") + new
        else:
            text = text.replace(anchor, new, 1)
        write_text(path, text, crlf)
        print(f"Step {step:2d} OK: {path}")

    print("\nDone. Expected now: pytest -> 34 failed, 268 passed (every failure is a finding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
