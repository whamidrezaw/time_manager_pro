from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings, get_settings
from app.db import get_events_collection
from app.utils.dates import calc_next_notify, expire_for_repeat, repeat_label, safe_zoneinfo, to_jalali
from app.utils.ids import object_id_str, safe_object_id

logger = logging.getLogger("tm_pro.reminders")


async def recover_stale_processing(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    events_coll = get_events_collection()

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.stale_processing_secs)
    result = await events_coll.update_many(
        {
            "notify_status": "processing",
            "processing_started_at": {"$lte": cutoff},
        },
        {
            "$set": {"notify_status": "pending"},
            "$unset": {"processing_started_at": ""},
        },
    )

    return int(result.modified_count)


def build_reminder_text(evt: dict) -> str:
    repeat = evt.get("repeat", "none")
    repeat_text = repeat_label(repeat)
    date_iso = evt.get("date_iso", "")
    jalali_date = to_jalali(date_iso)
    category = evt.get("category", "general")
    pin_mark = "📌 " if evt.get("pinned") else ""
    title = html.escape(evt.get("title", ""))

    return (
        f"🔔 <b>Reminder</b>\n"
        f"{pin_mark}{title}\n"
        f"📅 {date_iso}  •  {jalali_date}\n"
        f"🏷️ {html.escape(category.title())}\n"
        f"🔄 {repeat_text}"
    )


def build_reminder_keyboard(evt: dict, settings: Settings) -> InlineKeyboardMarkup:
    event_id = object_id_str(evt["_id"])
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={event_id}"
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏰ Snooze 1h", callback_data=f"snooze1h:{event_id}"),
                InlineKeyboardButton("📖 Open", url=deep_link),
            ]
        ]
    )


async def handle_snooze_callback(event_id: str, telegram_user_id: int, seconds: int) -> bool:
    try:
        oid = safe_object_id(event_id)
    except ValueError:
        return False

    events_coll = get_events_collection()
    new_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    result = await events_coll.find_one_and_update(
        {"_id": oid, "user_id": telegram_user_id},
        {"$set": {"next_notify_at": new_time, "notify_status": "pending"}},
    )
    return result is not None


async def process_due_reminders(
    bot: Bot,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    events_coll = get_events_collection()

    await recover_stale_processing(settings)

    now = datetime.now(timezone.utc)
    cursor = (
        events_coll.find(
            {
                "next_notify_at": {"$lte": now},
                "notify_status": "pending",
            }
        )
        .sort("next_notify_at", 1)
        .limit(settings.reminder_batch_size)
    )

    processed = 0

    async for evt in cursor:
        claimed = await events_coll.find_one_and_update(
            {
                "_id": evt["_id"],
                "notify_status": "pending",
            },
            {
                "$set": {
                    "notify_status": "processing",
                    "processing_started_at": now,
                }
            },
        )

        if not claimed:
            continue

        try:
            await bot.send_message(
                chat_id=evt["user_id"],
                text=build_reminder_text(evt),
                parse_mode="HTML",
                reply_markup=build_reminder_keyboard(evt, settings),
            )

            repeat = evt.get("repeat", "none")
            tz, _ = safe_zoneinfo(evt.get("tz_name", "UTC"))
            base_date = evt.get("event_ts_utc", now)
            if base_date.tzinfo is None:
                base_date = base_date.replace(tzinfo=timezone.utc)

            if repeat != "none":
                next_notify = calc_next_notify(
                    base_date=base_date,
                    repeat=repeat,
                    tz=tz,
                    reminder_hour=evt.get("reminder_hour", settings.default_reminder_hour),
                    reminder_minute=evt.get("reminder_minute", 0),
                )

                repeat_until = evt.get("repeat_until")
                stop_recurring = (
                    repeat_until is not None
                    and next_notify is not None
                    and next_notify.astimezone(tz).date().isoformat() > repeat_until
                )

                if stop_recurring:
                    await events_coll.update_one(
                        {"_id": evt["_id"]},
                        {
                            "$set": {"notify_status": "done", "next_notify_at": None},
                            "$unset": {"processing_started_at": ""},
                        },
                    )
                    processed += 1
                    continue

                expire_anchor = next_notify or now

                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "pending",
                            "next_notify_at": next_notify,
                            "expire_at": expire_for_repeat(expire_anchor, repeat),
                            "notify_attempts": 0,
                        },
                        "$unset": {"processing_started_at": ""},
                    },
                )
            else:
                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "done",
                            "next_notify_at": None,
                        },
                        "$unset": {"processing_started_at": ""},
                    },
                )

            processed += 1

        except Exception as exc:
            attempts = int(evt.get("notify_attempts", 0)) + 1
            status = "failed" if attempts >= 5 else "pending"

            await events_coll.update_one(
                {"_id": evt["_id"]},
                {
                    "$set": {
                        "notify_attempts": attempts,
                        "notify_status": status,
                    },
                    "$unset": {"processing_started_at": ""},
                },
            )
            logger.exception("Reminder send failed for event=%s error=%s", evt["_id"], exc)

    return processed