from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import Settings, get_settings
from app.services.reminders import handle_snooze_callback

logger = logging.getLogger("tm_pro.telegram")

router = APIRouter(prefix="/telegram", tags=["telegram"])

_START_TEXT = (
    "👋 <b>Welcome to TimeManager Pro!</b>\n\n"
    "Save birthdays, meetings and anything else you need to remember — and get "
    "the reminder right here in Telegram, on both the Gregorian and the Jalali "
    "calendar.\n\n"
    "Tap the button below to open the app and add your first event."
)

_HELP_TEXT = (
    "<b>TimeManager Pro — Help</b>\n\n"
    "/start — open the app and see the welcome message\n"
    "/help — show this message\n\n"
    "Everything else happens inside the Mini App: add, edit, pin and delete "
    "events, write notes, and pick the exact time you want to be reminded.\n\n"
    "When a reminder arrives, tap <b>⏰ Snooze 1h</b> to push it back an hour, "
    "or <b>📖 Open</b> to jump straight to that event."
)

_FALLBACK_TEXT = (
    "I don't understand plain text messages yet 🙂\n\n"
    "Send /help to see what I can do, or open the app with the button below."
)


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
    if update.callback_query:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_callback_query(update, bot)
    elif update.message:
        async with Bot(token=settings.bot_token) as bot:
            await _handle_message(update, bot, settings)

    return {"ok": True}


def build_open_app_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📅 Open TimeManager Pro", url=deep_link)]]
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


async def _handle_message(update: Update, bot: Bot, settings: Settings) -> None:
    message = update.message
    command = parse_command(message.text or "")

    if command == "/start":
        text = _START_TEXT
    elif command == "/help":
        text = _HELP_TEXT
    else:
        text = _FALLBACK_TEXT

    try:
        await bot.send_message(
            chat_id=message.chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=build_open_app_keyboard(settings),
        )
    except Exception:
        logger.exception("Failed to reply to chat_id=%s", message.chat_id)


async def _handle_callback_query(update: Update, bot: Bot) -> None:
    query = update.callback_query
    data = query.data or ""

    try:
        action, event_id = data.split(":", 1)
    except ValueError:
        await _safe_answer(bot, query.id, "Unknown action.")
        return

    if action == "snooze1h":
        # Authorization lives in handle_snooze_callback: it matches on both
        # _id and user_id (query.from_user.id), the same IDOR-safe pattern
        # used everywhere else in app/services/events.py.
        ok = await handle_snooze_callback(event_id, query.from_user.id, seconds=3600)
        text = "⏰ Snoozed — you'll be reminded again in 1 hour." if ok else "Couldn't snooze that event."
    else:
        text = "Unknown action."

    await _safe_answer(bot, query.id, text)


async def _safe_answer(bot: Bot, callback_query_id: str, text: str) -> None:
    try:
        await bot.answer_callback_query(callback_query_id, text=text)
    except Exception:
        logger.exception("Failed to answer callback query id=%s", callback_query_id)
