from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import Settings, get_settings
from app.services.reminders import handle_snooze_callback
from app.utils.i18n import resolve_language, t

logger = logging.getLogger("tm_pro.telegram")

router = APIRouter(prefix="/telegram", tags=["telegram"])



@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    settings = get_settings()

    # Confirms the request genuinely came from Telegram, not just anyone who
    # found this URL. Telegram echoes this header back on every webhook call
    # when a secret_token was set via set_webhook (see app/main.py lifespan).
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="BAD_SECRET_TOKEN")

    payload = await request.json()

    # Plain construction only — no network I/O, just needed so de_json can
    # attach a bot reference to the parsed objects.
    update = Update.de_json(payload, Bot(token=settings.bot_token))

    if update is None:
        return {"ok": True}

    # A live, initialized Bot is only needed for the actual API call, so it
    # stays scoped to the branches that really talk to Telegram.
    if update.my_chat_member:
        await _handle_membership(update, settings)
    elif update.callback_query:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_callback_query(update, bot)
    elif update.message:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_message(update, bot, settings)

    return {"ok": True}


def build_open_app_keyboard(settings: Settings, language: str = "en") -> InlineKeyboardMarkup:
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("open_app_button", language), url=deep_link)]]
    )


def parse_command(text: str) -> str:
    """Normalise a message into a bare command.

    '/start@MyBot some-payload' -> '/start'. Anything that is not a command
    (plain text, empty, a caption-less photo) -> ''.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return ""
    return parts[0].split("@", 1)[0].lower()


def parse_start_payload(text: str) -> str:
    """The argument after /start, or '' when there is none.

    Separate from parse_command on purpose: that function normalises a
    message down to the bare command and its tests guarantee the payload is
    dropped. Reading the payload is a different question, so it gets its own
    function instead of a second return value nobody else wants.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[0].startswith("/"):
        return ""
    return parts[1].strip()


async def _handle_message(update: Update, bot: Bot, settings: Settings) -> None:
    message = update.message
    command = parse_command(message.text or "")

    # No storage needed here: Telegram hands us the sender's language on every
    # update, so a user who switches language sees the change immediately.
    language = resolve_language(getattr(message.from_user, "language_code", None))

    if command == "/start":
        key = "start"
        await _attach_referral_from_start(message)
    elif command == "/help":
        key = "help"
    else:
        key = "fallback"

    try:
        await bot.send_message(
            chat_id=message.chat_id,
            text=t(key, language),
            parse_mode="HTML",
            reply_markup=build_open_app_keyboard(settings, language),
        )
    except Exception:
        logger.exception("Failed to reply to chat_id=%s", message.chat_id)


async def _handle_callback_query(update: Update, bot: Bot) -> None:
    query = update.callback_query
    data = query.data or ""
    language = resolve_language(getattr(query.from_user, "language_code", None))

    try:
        action, event_id = data.split(":", 1)
    except ValueError:
        await _safe_answer(bot, query.id, t("unknown_action", language))
        return

    if action == "snooze1h":
        # Authorization lives in handle_snooze_callback: it matches on both
        # _id and user_id (query.from_user.id), the same IDOR-safe pattern
        # used everywhere else in app/services/events.py.
        ok = await handle_snooze_callback(event_id, query.from_user.id, seconds=3600)
        text = t("snoozed" if ok else "snooze_failed", language)
    else:
        text = t("unknown_action", language)

    await _safe_answer(bot, query.id, text)


async def _safe_answer(bot: Bot, callback_query_id: str, text: str) -> None:
    try:
        await bot.answer_callback_query(callback_query_id, text=text)
    except Exception:
        logger.exception("Failed to answer callback query id=%s", callback_query_id)


async def _attach_referral_from_start(message) -> None:
    """Credit whoever's link brought this person here. Best effort.

    A failure here is invisible to the user by design: they came to use the
    bot, and the welcome message matters more than the bookkeeping.
    """
    code = parse_start_payload(message.text or "")
    if not code:
        return

    try:
        from app.services.referrals import attach_referrer, parse_ref_payload

        normalized = parse_ref_payload(code)
        if normalized:
            await attach_referrer(str(message.from_user.id), normalized)
    except Exception:
        logger.exception("referral attach failed chat_id=%s", message.chat_id)


async def _handle_membership(update, settings) -> None:
    """The bot was added to or removed from a group or channel.

    Telegram sends this as my_chat_member, which the webhook ignored until
    now — it only ever looked at messages. Registering the chat here is what
    makes it appear as a destination without anyone typing a command.
    """
    change = update.my_chat_member
    chat = change.chat
    status = change.new_chat_member.status

    try:
        from app.services.chats import (
            ACTIVE_STATUSES,
            ALLOWED_TYPES,
            chat_link,
            deactivate_chat,
            register_chat,
        )
        from app.utils.i18n import resolve_language, t

        if chat.type not in ALLOWED_TYPES:
            return

        if status not in ACTIVE_STATUSES:
            await deactivate_chat(chat.id)
            return

        added_by = str(change.from_user.id) if change.from_user else None
        record = await register_chat(chat.id, chat.type, chat.title or "", added_by)
        if not record or not added_by:
            return

        # Told in private, not in the group: the person who added the bot is
        # the one who needs to know what to do next, and a group does not need
        # a setup message from a bot it just met.
        language = resolve_language(
            getattr(change.from_user, "language_code", None) if change.from_user else None
        )
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=added_by,
                text=t("chat_connected", language).format(
                    title=chat.title or "",
                    link=chat_link(record["link_token"], settings),
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception:
        logger.exception("membership update failed chat_id=%s", getattr(chat, "id", "?"))
