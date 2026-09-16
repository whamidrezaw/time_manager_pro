#!/usr/bin/env python3
"""Batch 20, step 1 (A11Y-02): the Critical confirmation and detail-page fixes.

One file for two branches, chosen automatically:
  * fix/batch20-a11y (step 0 applied): moves the five Critical tests into
    tests/browser/test_a11y_destructive_actions.py and applies the fix.
    Expected afterwards: 29 failed, 274 passed.
  * a hotfix branch made from main: adds the browser harness and that same
    test file, then the same fix. Expected afterwards: 272 passed.

Everything both runs share is written byte-identically, so merging main back
into fix/batch20-a11y after the hotfix is conflict-free.

Run from the repository root:  python apply_batch20_step1.py
Every anchor is checked in a dry run first; nothing is written unless all hold.
A second run reports everything as already applied.
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
BATCH_MARKER = "tests/browser/test_a11y_browser.py"

# ── New files: the browser harness (identical to step 0) and the Critical tests ──
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
FILES['tests/browser/test_a11y_destructive_actions.py'] = r'''"""A11Y-02: confirmations and the detail page's destructive actions.

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
'''

# ── The fix. (path, marker meaning already applied, kind, old or start, end, new, scope) ──
FIX_0_OLD = r'''  <div id="confirmOverlay" class="confirm-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle">
      <div class="confirm-icon" id="confirmIcon">🗑️</div>
      <h3 id="confirmTitle" class="confirm-title">Delete Event?</h3>
      <p id="confirmText" class="confirm-text">This action cannot be undone.</p>
      <div class="confirm-actions">
        <button type="button" id="confirmCancelBtn" class="btn-secondary">Cancel</button>
        <button type="button" id="confirmOkBtn" class="btn-danger">Delete</button>
      </div>
    </div>
  </div>
'''
FIX_0_NEW = r'''  <!-- A native <dialog>: showModal() gives it focus, an inert page behind it
       and Escape. The id is the old overlay's, so nothing else had to change. -->
  <dialog id="confirmOverlay" class="confirm-dialog" role="alertdialog"
          aria-labelledby="confirmTitle" aria-describedby="confirmText">
    <div class="confirm-icon" id="confirmIcon" aria-hidden="true">🗑️</div>
    <h3 id="confirmTitle" class="confirm-title">Delete Event?</h3>
    <p id="confirmText" class="confirm-text">This action cannot be undone.</p>
    <div class="confirm-actions">
      <button type="button" id="confirmCancelBtn" class="btn-secondary" autofocus>Cancel</button>
      <button type="button" id="confirmOkBtn" class="btn-danger">Delete</button>
    </div>
  </dialog>
'''
FIX_1_OLD = r'''.confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
'''
FIX_1_NEW = r'''.confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

/* The confirm is a native <dialog> in the top layer, so the look the overlay
   used to give it is set here. Position stays explicit for WebViews that know
   nothing of <dialog>, where the element would otherwise be an inline box. */
dialog.confirm-dialog {
  position: fixed; inset: 0; z-index: 60;
  margin: auto; height: fit-content;
  color: var(--text);
}
dialog.confirm-dialog:not([open]) { display: none; }
dialog.confirm-dialog::backdrop {
  background: rgba(10, 12, 40, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
'''
FIX_2_OLD = r'''  /* ── Custom Confirm Dialog '''
FIX_2_NEW = r'''  /* ── Custom Confirm Dialog ──────────────────────────── */
  // A native <dialog> opened with showModal(): the browser moves focus into
  // it, makes the page behind it inert and closes it on Escape or a back
  // gesture. What this code guarantees is that every opening settles exactly
  // one decision, however the dialog ends up closed. Before, a dismissal that
  // bypassed the buttons left the OK handler armed, and the next confirmation
  // also deleted the event the user had cancelled.
  let settleOpenConfirm = null;

  function showConfirm({ title, text, okLabel = "Confirm", icon = "🗑️" }) {
    const dialog = els.confirmOverlay;
    if (!dialog) return Promise.resolve(false);

    // A new question answers any open one with "no" first, so there is never
    // more than one decision waiting behind the OK button.
    settleOpenConfirm?.(false);

    if (els.confirmTitle) els.confirmTitle.textContent = title;
    if (els.confirmText)  els.confirmText.textContent  = text;
    if (els.confirmOkBtn) els.confirmOkBtn.textContent = okLabel;
    const iconEl = dialog.querySelector(".confirm-icon");
    if (iconEl) iconEl.textContent = icon;

    return new Promise((resolve) => {
      const opener = document.activeElement;
      let settled = false;

      const onOk = () => settle(true);
      const onCancel = () => settle(false);
      // "close" is dispatched asynchronously. When a stale one from the
      // previous opening arrives, this dialog is already open again, and that
      // event is no answer to this question.
      const onClose = () => { if (!dialog.open) settle(false); };
      const onKey = (e) => {
        if (e.key !== "Escape") return;
        e.stopPropagation();
        settle(false);
      };

      function settle(result) {
        if (settled) return;
        settled = true;
        settleOpenConfirm = null;
        els.confirmOkBtn.removeEventListener("click", onOk);
        els.confirmCancelBtn.removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        dialog.removeEventListener("keydown", onKey);
        if (typeof dialog.close === "function") {
          if (dialog.open) dialog.close();          // the browser restores focus
        } else {
          dialog.removeAttribute("open");
          opener?.focus?.();
        }
        resolve(result);
      }

      settleOpenConfirm = settle;
      els.confirmOkBtn.addEventListener("click", onOk);
      els.confirmCancelBtn.addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);

      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        // WebViews without <dialog> (iOS before 15.4): the same single
        // decision, without the browser's modality.
        dialog.setAttribute("open", "");
        dialog.addEventListener("keydown", onKey);
        els.confirmCancelBtn.focus();
      }
    });
  }

'''
FIX_3_OLD = r'''        if (closeDatePicker()) return;
        if (els.confirmOverlay && !els.confirmOverlay.hidden) {
          els.confirmOverlay.hidden = true;
          return;
        }
        if (state.activeSheet) closeSheets();
'''
FIX_3_NEW = r'''        if (closeDatePicker()) return;
        // An open confirm closes itself on Escape and settles its own
        // decision; the sheet underneath has to stay where it is.
        if (els.confirmOverlay?.open) return;
        if (state.activeSheet) closeSheets();
'''
FIX_4_OLD = r'''    els.detailDeleteBtn?.addEventListener("click",      deleteCurrentEvent);
    els.detailPinBtn?.addEventListener("click",         toggleCurrentPin);
'''
FIX_4_NEW = r'''    // Wrapped, not passed directly: both take an optional event id, and a bare
    // reference receives the click event in its place, which matches no event
    // and made both buttons do nothing.
    els.detailDeleteBtn?.addEventListener("click",      () => deleteCurrentEvent());
    els.detailPinBtn?.addEventListener("click",         () => toggleCurrentPin());
'''
BATCH_DOC_OLD = r'''CONSTRAINTS.md says so, and REQUIREMENTS in docs/a11y/ names what each pins.
'''
BATCH_DOC_NEW = r'''CONSTRAINTS.md says so, and REQUIREMENTS in docs/a11y/ names what each pins.
The Critical A11Y-02 tests are in test_a11y_destructive_actions.py, which
ships to main as a hotfix.
'''

EDITS = [
    ('pyproject.toml', 'markers = [', 'replace', 'python_functions = ["test_*"]\n', None, 'python_functions = ["test_*"]\n# Browser tests run by default; leave them out of a fast local loop with\n# pytest -m "not browser". CI always runs them.\nmarkers = ["browser: real Chromium through Playwright and axe-core (tests/browser)"]\n', 'both'),
    ('requirements-dev.txt', 'playwright==', 'replace', 'mongomock-motor==0.0.36\n', None, 'mongomock-motor==0.0.36\n# Real Chromium for the accessibility tests in tests/browser. After install:\n#   python -m playwright install chromium\n# axe-core itself is vendored in tests/browser/vendor, not installed.\nplaywright==1.56.0\n', 'both'),
    ('.github/workflows/ci.yml', 'playwright install', 'replace', '      - name: Audit production dependencies\n', None, '      - name: Install Chromium for the browser tests\n        # tests/browser runs the real app in Chromium with vendored axe-core.\n        run: python -m playwright install --with-deps chromium\n      - name: Audit production dependencies\n', 'both'),
    ('templates/index.html', '<dialog id="confirmOverlay"', 'replace', FIX_0_OLD, None, FIX_0_NEW, 'both'),
    ('static/style.css', 'dialog.confirm-dialog::backdrop', 'replace', FIX_1_OLD, None, FIX_1_NEW, 'both'),
    ('static/app.js', 'let settleOpenConfirm = null;', 'between', FIX_2_OLD, '  /* ── API ', FIX_2_NEW, 'both'),
    ('static/app.js', 'if (els.confirmOverlay?.open) return;', 'replace', FIX_3_OLD, None, FIX_3_NEW, 'both'),
    ('static/app.js', '() => deleteCurrentEvent());', 'replace', FIX_4_OLD, None, FIX_4_NEW, 'both'),
    ('tests/browser/test_a11y_browser.py', 'test_a11y_destructive_actions.py, which', 'between',
     '# ── CRITICAL — data loss and actions that do nothing', '# ── Keyboard access ─', '', 'batch'),
    ('tests/browser/test_a11y_browser.py', 'test_a11y_destructive_actions.py, which', 'replace',
     BATCH_DOC_OLD, None, BATCH_DOC_NEW, 'batch'),
]


def read_text(path: str) -> tuple[str, bool]:
    raw = (ROOT / path).read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def write_text(path: str, text: str, crlf: bool = False) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def planned_edits(batch: bool) -> tuple[dict[str, tuple[str, bool]], list[str], list[str]]:
    """Dry run: every edited file's new text, what is already done, and what cannot be applied."""
    results: dict[str, tuple[str, bool]] = {}
    done: list[str] = []
    problems: list[str] = []
    originals: dict[str, str] = {}
    for path, marker, kind, old, end, new, scope in EDITS:
        if scope == "batch" and not batch:
            continue
        if not (ROOT / path).exists():
            problems.append(f"{path} is missing")
            continue
        if path not in results:
            text, crlf = read_text(path)
            originals[path] = text
            results[path] = (text, crlf)
        text, crlf = results[path]
        if marker in originals[path]:
            done.append(path)
            continue
        if kind == "replace":
            if text.count(old) != 1:
                problems.append(f"{path}: anchor not found exactly once: {old.strip().splitlines()[0]!r}")
                continue
            text = text.replace(old, new)
        else:
            if text.count(old) != 1 or text.count(end) != 1:
                problems.append(f"{path}: anchors not found exactly once: {old.strip()!r} ... {end.strip()!r}")
                continue
            text = text[:text.index(old)] + new + text[text.index(end):]
        results[path] = (text, crlf)
    changed = {p: r for p, r in results.items() if r[0] != originals[p]}
    return changed, sorted(set(done)), problems


def main() -> int:
    if not (ROOT / "app" / "main.py").exists() or not (ROOT / "tests" / "harness.py").exists():
        print("Run this from the time_manager_pro repository root.")
        return 1
    batch = (ROOT / BATCH_MARKER).exists()
    print("Mode:", "fix/batch20-a11y (step 0 present)" if batch else "hotfix on main (adds the harness)")

    problems = [f"{p} exists with different content; resolve it by hand first"
                for p, content in FILES.items() if (ROOT / p).exists() and read_text(p)[0] != content]
    changed, done, edit_problems = planned_edits(batch)
    problems += edit_problems
    if problems:
        print("Nothing was changed:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1

    axe = ROOT / AXE_TARGET
    axe_data = None
    if not (axe.exists() and hashlib.sha256(axe.read_bytes()).hexdigest() == AXE_SHA256):
        with urllib.request.urlopen(AXE_URL, timeout=60) as response:
            archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
        axe_data = archive.extractfile(AXE_MEMBER).read()
        if hashlib.sha256(axe_data).hexdigest() != AXE_SHA256:
            print("axe-core checksum mismatch; nothing was changed.")
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
    if axe_data is None:
        print(f"Step {step:2d} already applied: {AXE_TARGET}")
    else:
        axe.parent.mkdir(parents=True, exist_ok=True)
        axe.write_bytes(axe_data)
        print(f"Step {step:2d} OK: {AXE_TARGET} (sha256 verified)")
    for path in sorted(set(p for p, *_ in EDITS)):
        if path not in changed and path not in done:
            continue
        step += 1
        if path in changed:
            write_text(path, *changed[path])
            print(f"Step {step:2d} OK: {path}")
        else:
            print(f"Step {step:2d} already applied: {path}")

    expected = "29 failed, 274 passed" if batch else "272 passed, no failures"
    print(f"\nDone. Expected now: pytest -> {expected}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
