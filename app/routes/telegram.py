from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import Settings, get_settings
from app.services.admin import ADMIN_COMMANDS, handle_admin_command, is_admin, remember_username
from app.services.erasure import delete_user_data
from app.services.reminders import handle_snooze_callback
from app.services.telegram_bot import telegram_bot
from app.utils.i18n import resolve_language, t
from app.utils.keyboards import build_open_app_keyboard

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
    if not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret
    ):
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
        async with telegram_bot(settings, Bot) as bot:
            await _handle_callback_query(update, bot)
    elif update.message:
        async with telegram_bot(settings, Bot) as bot:
            await _handle_message(update, bot, settings)

    return {"ok": True}


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

    # So the admin can name this user as @username (Batch 20).
    sender_id = getattr(message.from_user, "id", None)
    await remember_username(sender_id, getattr(message.from_user, "username", None))
    if command in ADMIN_COMMANDS and is_admin(sender_id, settings):
        try:
            reply = await handle_admin_command(message.text or "", str(sender_id), settings)
            await bot.send_message(chat_id=message.chat_id, text=reply)
        except Exception:
            logger.exception("Admin command failed: %s", command)
        return

    # No storage needed here: Telegram hands us the sender's language on every
    # update, so a user who switches language sees the change immediately.
    language = resolve_language(getattr(message.from_user, "language_code", None))

    if command == "/deletemydata":
        await _ask_before_deleting(bot, message, language)
        return
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

    if action == "delme":
        await _confirm_deleting(bot, query, event_id, language)
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


CONFIRM_FOR = timedelta(minutes=10)


async def _ask_before_deleting(bot: Bot, message, language: str) -> None:
    """/deletemydata asks first, in a private chat, with a button bound to the
    sender; nothing is erased until that button is pressed (ADR 0017)."""
    if getattr(message.chat, "type", "") != "private":
        text, markup = t("delete_private_only", language), None
    else:
        text = t("delete_ask", language)
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("delete_yes", language), callback_data=f"delme:{message.from_user.id}"),
            InlineKeyboardButton(t("delete_no", language), callback_data="delme:no"),
        ]])
    try:
        await bot.send_message(chat_id=message.chat_id, text=text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to ask before deleting, chat_id=%s", message.chat_id)


async def _confirm_deleting(bot: Bot, query, answer: str, language: str) -> None:
    if answer == "no":
        await _replace(bot, query, t("delete_cancelled", language))
    elif str(query.from_user.id) != answer:
        await _safe_answer(bot, query.id, t("delete_not_yours", language))
        return
    elif query.message is None or datetime.now(timezone.utc) - query.message.date > CONFIRM_FOR:
        await _replace(bot, query, t("delete_expired", language))
    else:
        await delete_user_data(answer)
        await _replace(bot, query, t("delete_done", language))
    await _safe_answer(bot, query.id, "")


async def _replace(bot: Bot, query, text: str) -> None:
    """Edit the confirmation in place, which also takes its buttons away."""
    try:
        await bot.edit_message_text(text=text, chat_id=query.message.chat_id,
                                    message_id=query.message.message_id)
    except Exception:
        logger.exception("Failed to edit the confirmation")


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
        async with telegram_bot(settings, Bot) as bot:
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
