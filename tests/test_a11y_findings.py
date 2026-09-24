"""One test per accessibility finding that needs no browser (Batch 20 review).

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


def test_the_date_fields_say_they_open_a_dialog():
    """A11Y-08 / 4.1.2. A readonly field that opens a picker should say so.

    aria-haspopup="dialog" is what tells a screen reader that Enter on the
    field opens something, rather than leaving a read-only text box that seems
    to do nothing.
    """
    nodes = parse(INDEX.read_text(encoding="utf-8"))
    pickers = [node for node in nodes if "field-picker" in (node.attrs.get("class") or "").split()]
    missing = [node.attrs.get("id") for node in pickers if node.attrs.get("aria-haspopup") != "dialog"]

    assert pickers and not missing, f"picker fields without aria-haspopup=dialog: {missing}"


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

    Since Batch 20 the brand has two roles: --brand is a fill with white text
    on it, in every theme, and --brand-ink is the brand as text, which dark
    themes lighten. The Telegram-light case includes the dark media query
    underneath it, as on a phone in dark mode.
    """
    css = STYLESHEET.read_text(encoding="utf-8")
    base = _block(css, r"^:root\s*\{(.*?)^\}")
    media_dark = _block(css, r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}")
    tg_dark = _block(css, r'^:root\[data-tg-scheme="dark"\]\s*\{(.*?)^\}')
    tg_light = _block(css, r'^:root\[data-tg-scheme="light"\]\s*\{(.*?)^\}')
    themes = {
        "light": base,
        "light [data-tg-scheme] on a dark phone": {**base, **media_dark, **tg_light},
        "dark (media query)": {**base, **media_dark},
        "dark [data-tg-scheme]": {**base, **tg_dark},
    }
    pairs = [
        ("text-muted", "surface", "labels and subtitles on cards and sheets"),
        ("text-muted", "bg", "the header subtitle"),
        ("brand-ink", "surface", "the active tab and accent text"),
        ("brand-text", "brand", "text on primary buttons and today"),
    ]

    failures = []
    for theme, tokens in themes.items():
        for foreground, background, where in pairs:
            if foreground not in tokens:
                failures.append(f"{theme}: no --{foreground} ({where})")
                continue
            ratio = contrast_ratio(_hex(tokens, foreground), _hex(tokens, background))
            if ratio < 4.5:
                failures.append(f"{theme}: --{foreground} on --{background} = {ratio:.2f}:1 ({where})")

    assert not failures, "below 4.5:1:\n" + "\n".join(failures)


def test_field_errors_have_a_colour_that_meets_wcag_aa():
    """A11Y-06: the message under a field has its own colour. --danger is
    #ef4444, 3.8:1 on white, too faint for small text; --error-text must reach
    4.5:1 on the sheet's surface in every theme.

    The Telegram-light case includes the dark media query underneath it: on a
    phone in dark mode with Telegram set to light, only the light block can
    take a dark value back.
    """
    css = STYLESHEET.read_text(encoding="utf-8")
    base = _block(css, r"^:root\s*\{(.*?)^\}")
    media_dark = _block(css, r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}")
    tg_dark = _block(css, r'^:root\[data-tg-scheme="dark"\]\s*\{(.*?)^\}')
    tg_light = _block(css, r'^:root\[data-tg-scheme="light"\]\s*\{(.*?)^\}')
    themes = {
        "light": base,
        "dark (media query)": {**base, **media_dark},
        "dark [data-tg-scheme]": {**base, **media_dark, **tg_dark},
        "light [data-tg-scheme] on a dark phone": {**base, **media_dark, **tg_light},
    }

    failures = []
    for theme, tokens in themes.items():
        if "error-text" not in tokens:
            failures.append(f"{theme}: no --error-text")
            continue
        ratio = contrast_ratio(_hex(tokens, "error-text"), _hex(tokens, "surface"))
        if ratio < 4.5:
            failures.append(f"{theme}: --error-text on --surface = {ratio:.2f}:1")

    assert not failures, "\n".join(failures)


def _over(rgba: tuple, background: str) -> str:
    """A translucent tint laid over an opaque background, as the browser paints it."""
    r, g, b, a = rgba
    base = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a * v + (1 - a) * w):02x}" for v, w in zip((r, g, b), base))


def test_accent_text_meets_wcag_aa_on_its_own_background():
    """A11Y-05, second half: every rule that puts coloured text on a tint of
    its own (category and pin badges, urgency badges, the detail page's
    buttons) reads at 4.5:1 or better, in light and dark.

    Read from the stylesheet itself, so a badge added later is checked
    without being listed. axe could not do this: it marked the animated and
    overlapping badges incomplete and never reported that "3 days left" read
    at 2.7:1. Dark themes are also checked on Telegram's common dark surfaces,
    which are a shade lighter than the app's own.
    """
    css = STYLESHEET.read_text(encoding="utf-8")
    base = _block(css, r"^:root\s*\{(.*?)^\}")
    media_dark = _block(css, r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}")
    tg_dark = _block(css, r'^:root\[data-tg-scheme="dark"\]\s*\{(.*?)^\}')
    tg_light = _block(css, r'^:root\[data-tg-scheme="light"\]\s*\{(.*?)^\}')
    themes = {
        "light": ({**base}, []),
        "light [data-tg-scheme] on a dark phone": ({**base, **media_dark, **tg_light}, []),
        "dark (media query)": ({**base, **media_dark}, ["#212121", "#17212b"]),
        "dark [data-tg-scheme]": ({**base, **tg_dark}, ["#212121", "#17212b"]),
    }
    rules = []
    for selector, body in re.findall(r"^([.#][^{}\n]+?)\s*\{([^{}]*)\}", css, re.M):
        tint = re.search(r"background:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", body)
        colour = re.search(
            r"(?<![-\w])color:\s*(#[0-9a-fA-F]{6}|var\(--[\w-]+(?:,\s*#[0-9a-fA-F]{6})?\))", body)
        if tint and colour:
            rgba = tuple(int(tint.group(i)) for i in (1, 2, 3)) + (float(tint.group(4)),)
            rules.append((selector.strip(), colour.group(1), rgba))
    labels = re.findall(r"^(\.section-\w+\s+\.event-section-label)\s*\{\s*color:\s*([^;]+);", css, re.M)
    assert len(rules) >= 19 and len(labels) >= 3, (len(rules), len(labels))

    def resolve(value: str, tokens: dict) -> str:
        if value.startswith("#"):
            return value
        name = re.match(r"var\(--([\w-]+)", value).group(1)
        return _hex({**tokens, "_": value}, "_") if name.startswith("tg-") else _hex(tokens, name)

    failures = []
    for theme, (tokens, telegram_surfaces) in themes.items():
        surfaces = [_hex(tokens, "surface"), *telegram_surfaces]
        for selector, value, rgba in rules:
            text = resolve(value, tokens)
            worst = min(contrast_ratio(text, _over(rgba, surface)) for surface in surfaces)
            if worst < 4.5:
                failures.append(f"{theme}: {selector} {text} = {worst:.2f}:1")
        for selector, value in labels:
            text = resolve(value.strip(), tokens)
            ratio = contrast_ratio(text, _hex(tokens, "bg"))
            if ratio < 4.5:
                failures.append(f"{theme}: {selector} {text} on --bg = {ratio:.2f}:1")

    assert not failures, f"{len(failures)} below 4.5:1:\n" + "\n".join(failures)


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
