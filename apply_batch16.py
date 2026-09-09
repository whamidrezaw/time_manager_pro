#!/usr/bin/env python3
"""
apply_batch16.py — TimeManager Pro, Batch 16 (an invite you can see)

Sending a shared event used to post a t.me deep link, and Telegram previews
those as the bot's own profile card. Nobody taps a bot profile to find out
about a birthday. So an invite now travels as the public countdown URL, which
carries the rendered card in its Open Graph tags, and the page behind it grew
a button that adds the event to the visitor's own calendar.

  * the public link switches itself on the first time a sheet is opened
  * an invite posts the picture, not the bot
  * the countdown page can add the event to your events
  * the invite screen inside the app shows the card too
  * copy and share stopped being two grey buttons

Run once from the repository root:

    python apply_batch16.py --check   # dry run, writes nothing
    python apply_batch16.py           # apply

Requires Batch 12c. Safe to run twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRY_RUN = "--check" in sys.argv

PENDING: dict[Path, str] = {}
LOG: list[tuple[str, str]] = []
FAILED = False


def _note(status: str, message: str) -> None:
    LOG.append((status, message))


def _fail(message: str) -> None:
    global FAILED
    FAILED = True
    _note("FAIL", message)


def _current(path: Path) -> str | None:
    if path in PENDING:
        return PENDING[path]
    return path.read_text(encoding="utf-8") if path.exists() else None


def patch(rel: str, old: str, new: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found — are you in the repository root?")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    count = text.count(old)
    if count != 1:
        _fail(f"{rel}: anchor for '{label}' matched {count} times, expected 1 "
              "— is Batch 12c applied?")
        return

    PENDING[path] = text.replace(old, new, 1)
    _note(" OK ", f"{rel}: {label}")


def append(rel: str, addition: str, marker: str, label: str) -> None:
    path = ROOT / rel
    text = _current(path)

    if text is None:
        _fail(f"{rel}: file not found")
        return
    if marker in text:
        _note("SKIP", f"{rel}: {label} (already applied)")
        return

    PENDING[path] = text.rstrip("\n") + "\n" + addition
    _note(" OK ", f"{rel}: {label}")


def flush() -> None:
    for path, content in PENDING.items():
        path.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 1. An invite link needs a picture, so it turns the public link on
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/services/share_group.py",
    old="""    return {
        "success": True,
        "token": token,
        "invite_url": invite_url(token, settings),
        "members": await member_count(share_id),
    }
""",
    new="""    # An invite is only worth sending if it shows the event, and the picture
    # lives behind the public token. Turning that on here rather than asking
    # is deliberate: someone who just pressed "share this event with someone"
    # has already decided to show it to that someone.
    from app.services.sharing import card_url, describe, public_url, set_share_state

    fresh = await get_events_collection().find_one({"_id": event["_id"]})
    share = describe(fresh or {}, settings)
    if not share["enabled"]:
        share = await set_share_state(user_id, str(event["_id"]), True, settings)

    public_token = share.get("token")

    return {
        "success": True,
        "token": token,
        "invite_url": invite_url(token, settings),
        # What actually gets posted: Telegram previews this one as the card.
        "public_url": public_url(public_token, settings) if public_token else None,
        "card_url": card_url(public_token, settings) if public_token else None,
        "members": await member_count(share_id),
    }
""",
    marker='"public_url": public_url(public_token, settings)',
    label="an invite link carries the card",
)

patch(
    "app/routes/sharegroup.py",
    old="""    owner_id = str(origin.get("user_id"))
    return {
        "success": True,
        "title": origin.get("title", ""),
""",
    new="""    owner_id = str(origin.get("user_id"))

    # Shown on the invite screen so the decision is made looking at the event
    # rather than at a name and a date in plain text.
    from app.services.sharing import card_url as public_card_url

    token_public = origin.get("public_token") if origin.get("public_enabled") else None

    return {
        "success": True,
        "card_url": public_card_url(token_public, settings) if token_public else None,
        "title": origin.get("title", ""),
""",
    marker="public_card_url",
    label="the invite carries a card url",
)

# ══════════════════════════════════════════════════════════════════════
# 2. The countdown page can add the event to your own calendar
# ══════════════════════════════════════════════════════════════════════

patch(
    "app/routes/share.py",
    old="""            "open_label": t("share_open_button", language),
""",
    new="""            # Only set when the event is part of a share group: this is the
            # button that turns a link someone forwarded into an event of
            # your own, without going through the bot's profile page first.
            "join_url": (
                f"https://t.me/{settings.telegram_bot_username}/"
                f"{settings.telegram_mini_app_short_name}"
                f"?startapp=s_{event['share_token']}"
                if event.get("share_token") and event.get("share_role") == "owner"
                else None
            ),
            "join_label": t("share_join_button", language),
            "open_label": t("share_open_button", language),
""",
    marker='"join_url"',
    label="a join link on the public page",
)

patch(
    "templates/countdown.html",
    old="""  <a class="cta" href="{{ miniapp_url }}">{{ open_label }}</a>
""",
    new="""  {% if join_url %}<a class="cta" href="{{ join_url }}">{{ join_label }}</a>
  <a class="cta cta-quiet" href="{{ miniapp_url }}">{{ open_label }}</a>
  {% else %}<a class="cta" href="{{ miniapp_url }}">{{ open_label }}</a>
  {% endif %}
""",
    marker="cta-quiet",
    label="the join button on the page",
)

patch(
    "templates/countdown.html",
    old="""    a.cta:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
""",
    new="""    a.cta:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
    a.cta-quiet {
      background: transparent; color: #fff;
      border: 1.5px solid rgba(255, 255, 255, 0.55);
      font-weight: 500;
    }
""",
    marker="cta-quiet {",
    label="style the secondary button",
)

patch(
    "app/utils/i18n.py",
    old="""    "share_open_button": {"en": "Open in Telegram", "fa": "باز کردن در تلگرام"},
""",
    new="""    "share_open_button": {"en": "Open in Telegram", "fa": "باز کردن در تلگرام"},
    "share_join_button": {
        "en": "Add to my events",
        "fa": "به رویدادهای من اضافه کن",
    },
""",
    marker="share_join_button",
    label="copy for the join button",
)

# ══════════════════════════════════════════════════════════════════════
# 3. The share sheet
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/share.js",
    old="""    try {
      state = await api("/api/share/state", { event_id: current });
      render();
    } catch (_) {
      els.hint.textContent = t("Could not load sharing.");
    }
  }
""",
    new="""    try {
      state = await api("/api/share/state", { event_id: current });

      // On by default now. Opening this sheet is already the decision to show
      // the event to someone, and making that a separate switch only meant the
      // first tap on "send" did nothing. It is announced rather than silent,
      // and the switch is right there to turn it back off.
      if (!state.enabled) {
        state = await api("/api/share/toggle", { event_id: current, enabled: true });
        els.hint.textContent = t("Public link is on. Switch it off any time.");
      }
      render();
    } catch (_) {
      els.hint.textContent = t("Could not load sharing.");
    }
  }
""",
    marker="Public link is on. Switch it off any time.",
    label="public link on by default",
)

patch(
    "static/share.js",
    old="""      var group = await api("/api/group/link", { event_id: current });
      var url = "https://t.me/share/url?url=" + encodeURIComponent(group.invite_url);
""",
    new="""      var group = await api("/api/group/link", { event_id: current });
      // The public page, not the t.me deep link: Telegram renders that one as
      // the bot's profile card, and this one as the event itself. The page
      // behind it carries the button that adds the event to their calendar.
      var target = group.public_url || group.invite_url;
      var url = "https://t.me/share/url?url=" + encodeURIComponent(target)
        + "&text=" + encodeURIComponent(t("Let's keep this one in sync."));
""",
    marker="group.public_url || group.invite_url",
    label="share the picture, not the bot",
)

patch(
    "static/share.js",
    old="""    "Share the event itself with someone": "این رویداد را با کسی مشترک کن",
""",
    new="""    "Share the event itself with someone": "این رویداد را با کسی مشترک کن",
    "Public link is on. Switch it off any time.": "لینک عمومی روشن شد. هر وقت خواستی خاموشش کن.",
    "Let's keep this one in sync.": "بیا این را با هم هماهنگ نگه داریم.",
""",
    marker="لینک عمومی روشن شد",
    label="Persian for the new lines",
)

# ══════════════════════════════════════════════════════════════════════
# 4. The invite screen shows the card
# ══════════════════════════════════════════════════════════════════════

patch(
    "static/invite.js",
    old="""      '<div class="invite-card" role="dialog" aria-modal="true">' +
        '<span class="invite-tag">' + t("Shared event") + "</span>" +
""",
    new="""      '<div class="invite-card" role="dialog" aria-modal="true">' +
        (invite.card_url
          ? '<img class="invite-image" alt="" src="' + invite.card_url + '" />'
          : "") +
        '<span class="invite-tag">' + t("Shared event") + "</span>" +
""",
    marker="invite-image",
    label="show the card on the invite screen",
)

# ══════════════════════════════════════════════════════════════════════
# 5. Colour
# ══════════════════════════════════════════════════════════════════════

STYLES = r'''
/* ── Batch 16 — a share sheet with some colour in it ─── */

/* Copy and share were two grey buttons of equal weight, which told nobody
   what to press. Now the primary action carries the brand and the secondary
   one is tinted rather than empty. */
.shr-actions #shrCopy {
  border-color: transparent;
  background: rgba(91, 108, 248, 0.12);
  color: var(--brand);
  font-weight: 800;
}
.shr-actions #shrCopy:active { background: rgba(91, 108, 248, 0.2); }

.shr-group {
  border: none;
  background: linear-gradient(160deg, #ec4899, #be185d);
  color: #fff;
  font-weight: 800;
}
.shr-group:active { filter: brightness(0.94); }
.shr-group:disabled { opacity: 0.6; }

.invite-image {
  display: block;
  width: 100%; height: auto;
  margin: -8px 0 16px;
  border-radius: var(--r-md);
}
'''

append("static/style.css", STYLES, "Batch 16 — a share sheet", "share sheet colour")


# ══════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    width = max((len(message) for _, message in LOG), default=40)

    print("\n  TimeManager Pro — Batch 16 (an invite you can see)\n")
    print("  " + "─" * min(width + 8, 76))
    for status, message in LOG:
        print(f"  [{status}] {message}")
    print("  " + "─" * min(width + 8, 76))

    if FAILED:
        print("\n  Nothing was written. Fix the files named above and run again.\n")
        return 1

    if DRY_RUN:
        print(f"\n  Dry run: {len(PENDING)} file(s) would change. Nothing written.\n")
        return 0

    flush()
    print(f"\n  {len(PENDING)} file(s) written. Next:\n")
    print("      ruff check . && pytest")
    print("      git add -A && git commit -m 'Batch 16: an invite that shows the event'")
    print()
    print("  Send a shared event to a chat: the preview should be the card now,")
    print("  and the page behind it should offer to add the event.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
