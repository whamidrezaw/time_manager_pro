"""Real-browser harness for the Batch 20 accessibility findings.

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
import importlib
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
import telegram
import uvicorn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.config import get_settings
from app.services.auth import compute_telegram_hash
from app.services.share_group import start_group
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
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        # The app builds absolute URLs for share cards and public links from
        # WEBAPP_BASE_URL. Pointing it at this server is what production does:
        # one origin serves the page and its images, so img-src 'self' accepts
        # them. Left as it was, the card preview is refused by the CSP.
        self._base_url_before = os.environ.get("WEBAPP_BASE_URL")
        os.environ["WEBAPP_BASE_URL"] = self.url
        get_settings.cache_clear()

        from app.main import app

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
        if self._base_url_before is None:
            os.environ.pop("WEBAPP_BASE_URL", None)
        else:
            os.environ["WEBAPP_BASE_URL"] = self._base_url_before
        get_settings.cache_clear()


async def _inviter_name(user_id, settings=None) -> str:
    """Stands in for Telegram's getChat, the one outside call the join card makes."""
    return "Ava"


# Every module of the web app that talks to Telegram. The live server runs with
# a stand-in in each; test_harness.py keeps this list complete.
TELEGRAM_MODULES = (
    "app.main",
    "app.routes.events",
    "app.routes.tasks",
    "app.routes.telegram",
    "app.services.health",
    "app.services.referrals",
)


class FakeTelegramBot:
    """Stands in for telegram.Bot while the live server runs: no test reaches
    the network. Batch 20 found saving an event waiting on api.telegram.org
    with the test token, so a test was as fast as the network happened to be.
    Messages land in `sent` instead of a chat; any other call does nothing."""

    sent: list[dict] = []
    delay = 0  # seconds a message takes to go out, for a test that needs a slow Telegram

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, **kwargs):
        if FakeTelegramBot.delay:
            await asyncio.sleep(FakeTelegramBot.delay)
        FakeTelegramBot.sent.append(kwargs)

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return None
        return call


@pytest.fixture(scope="module")
def live_server():
    import app.routes.sharegroup as sharegroup

    install_fake_db()
    real_name_lookup = sharegroup.get_first_name
    sharegroup.get_first_name = _inviter_name
    # Two ways in: modules that imported Bot at the top hold their own name for
    # it, and imports inside a function (health, referrals) read the package's
    # at call time. Both are stood in for, and both are put back.
    real_package_bot = telegram.Bot
    telegram.Bot = FakeTelegramBot
    real_bots = {}
    for name in TELEGRAM_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "Bot"):
            real_bots[name] = module.Bot
            module.Bot = FakeTelegramBot
    server = LiveServer()
    server.start()
    yield server
    server.stop()
    for name, bot in real_bots.items():
        importlib.import_module(name).Bot = bot
    telegram.Bot = real_package_bot
    sharegroup.get_first_name = real_name_lookup
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


def telegram_stub(user_id: int, lang: str, scheme: str, theme: dict | None, start_param: str = "") -> str:
    """Only the surface static/*.js actually touches."""
    return f"""
window.Telegram = {{ WebApp: {{
  initData: {json.dumps(signed_init_data(user_id, lang))},
  initDataUnsafe: {{ user: {{ id: {user_id}, first_name: "Test", language_code: {json.dumps(lang)} }},
                    start_param: {json.dumps(start_param)} }},
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
  // Handlers are kept so a test can press "back" the way Telegram does.
  BackButton: {{ show: function () {{}}, hide: function () {{}},
    onClick: function (handler) {{
      (window.__tgBackHandlers = window.__tgBackHandlers || []).push(handler);
    }},
    offClick: function (handler) {{
      window.__tgBackHandlers = (window.__tgBackHandlers || [])
        .filter(function (h) {{ return h !== handler; }});
    }} }},
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
                onboarding_seen=True, telegram=True, invite=False, extra_events=0):
        user_id = next(_users)
        seeded = [live_server.run(seed_event(user_id=str(user_id), **event)) for event in default_events()]
        for n in range(extra_events):  # fillers, for a user near or at the event limit
            live_server.run(seed_event(user_id=str(user_id), title=f"Filler {n + 1}",
                                       date_iso=(date.today() + timedelta(days=60 + n)).isoformat(),
                                       event_ts_utc=utc(days=60 + n)))
        start_param = ""
        if invite:
            # Opened from a friend's ?startapp=s_<token> link to "Book club".
            owner = str(next(_users))
            shared = live_server.run(seed_event(
                user_id=owner, title="Book club", category="general",
                date_iso=(date.today() + timedelta(days=9)).isoformat(), event_ts_utc=utc(days=9),
            ))
            start_param = "s_" + live_server.run(start_group(owner, shared["_id"]))["token"]

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

        sdk = telegram_stub(user_id, lang, scheme, theme, start_param) if telegram else ""
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


def reshow_open_dialogs_for_axe(page) -> None:
    """axe-core's stacking model does not know the browser's top layer: every
    line of text inside an open modal <dialog> comes back as "overlapped by
    another element" (naming no element), so a contrast check there would pass
    without looking. Showing the same dialogs again non-modally leaves their
    markup and pixels as they are and lets axe measure them. Modality itself is
    tested elsewhere, in test_a11y_dialogs.py.

    A dialog that is closed and shown again at once is still open when its
    queued "close" event arrives, so TMModal ignores that event."""
    page.evaluate("() => document.querySelectorAll('dialog[open]').forEach((d) => { d.close(); d.show(); })")


@pytest.fixture
def axe(axe_source):
    """Returns serious and critical WCAG 2.1 A/AA violations for the page as it is now."""

    def run(page) -> list[dict]:
        reshow_open_dialogs_for_axe(page)
        page.evaluate(axe_source)  # evaluate() is not subject to the page's CSP
        options = {"runOnly": {"type": "tag", "values": AXE_TAGS}, "resultTypes": ["violations"]}
        result = page.evaluate("async (options) => await axe.run(document, options)", options)
        return [v for v in result["violations"] if v["impact"] in ("serious", "critical")]

    return run

