"""The browser harness is hermetic: no test reaches the network.

Found in Batch 20: saving an event waits for Telegram's confirmation message
before it answers, and the harness let that call go out to api.telegram.org
with the test token. A test's speed then depended on the network, which is how
the keyboard-only save flaked on Windows; a Telegram slowed to 6 s reproduced
it exactly. The harness now stands in for Telegram, and these tests pin that.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

import tests.browser.conftest as harness

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]


def stand_in():
    bot = getattr(harness, "FakeTelegramBot", None)
    if bot is None:
        pytest.fail("the harness has no Telegram stand-in yet")
    return bot


def test_every_module_that_talks_to_telegram_is_stood_in_for():
    """A module that later starts talking to Telegram has to join the list,
    or its calls would go out to the network again."""
    stand_in()
    talking = {
        ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for path in (ROOT / "app").rglob("*.py")
        if re.search(r"\bBot\(token=", path.read_text(encoding="utf-8"))
    }
    assert talking == set(harness.TELEGRAM_MODULES), (
        f"not stood in for: {sorted(talking - set(harness.TELEGRAM_MODULES))}")


def test_saving_an_event_confirms_it_in_telegram_without_the_network(open_app):
    """The confirmation message is still sent, and still checked: to the
    stand-in, which records it, instead of to api.telegram.org."""
    bot = stand_in()
    bot.sent.clear()
    page = open_app()
    page.click("#openComposerBtn")
    page.fill("#title", "Hermetic lunch")
    page.click("#date")
    page.click("#dpConfirm")
    page.click("#saveEventBtn")

    expect(page.locator("#composerSheet")).to_be_hidden(timeout=5000)
    assert any("Hermetic lunch" in str(message.get("text", "")) for message in bot.sent), bot.sent
