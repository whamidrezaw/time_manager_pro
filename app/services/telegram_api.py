from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("tm_pro.telegram_api")

TIMEOUT = 15.0


def build_photo_result(card: str, link: str, title: str, caption: str,
                       button: str) -> dict:
    """An InlineQueryResultPhoto carrying the card and a way back to the bot.

    The button is not decoration: every shared card is supposed to be a door
    back into the bot, which is the entire reason Batch 12 exists.
    """
    return {
        "type": "photo",
        "id": "event-card",
        "photo_url": card,
        "thumbnail_url": card,
        "photo_width": 1080,
        "photo_height": 1080,
        "title": title,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": button, "url": link}]]},
    }


async def save_prepared_inline_message(
    user_id: str,
    result: dict,
    settings: Settings | None = None,
) -> str:
    """Wrap one inline result so the Mini App can hand it to shareMessage.

    Called over plain HTTP rather than through python-telegram-bot: this method
    arrived in Bot API 8.0 and the pinned 21.1 release does not expose it.
    Upgrading the library would touch the reminder worker and the webhook,
    which is a far larger blast radius than one POST.
    """
    settings = settings or get_settings()
    url = f"https://api.telegram.org/bot{settings.bot_token}/savePreparedInlineMessage"

    payload = {
        "user_id": int(user_id),
        "result": result,
        "allow_user_chats": True,
        "allow_group_chats": True,
        "allow_channel_chats": True,
        "allow_bot_chats": False,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(url, json=payload)

    try:
        body = response.json()
    except ValueError:
        logger.error("savePreparedInlineMessage returned non-JSON (%s)", response.status_code)
        raise RuntimeError("PREPARE_FAILED") from None

    if not body.get("ok"):
        # Old Bot API on a self-hosted server, or a photo URL Telegram could
        # not fetch. Either way the Mini App has a fallback, so this is logged
        # and reported rather than raised as a 500.
        logger.warning("savePreparedInlineMessage refused: %s", body.get("description"))
        raise RuntimeError("PREPARE_FAILED")

    return body["result"]["id"]
