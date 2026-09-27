"""Telegram keyboards shared by routes and services (Batch 29, review R3).

build_open_app_keyboard lived in the webhook route, so the referral service
imported a route, hidden in a function to dodge the import cycle that made.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.utils.i18n import t


def build_open_app_keyboard(settings: Settings, language: str = "en") -> InlineKeyboardMarkup:
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("open_app_button", language), url=deep_link)]]
    )
