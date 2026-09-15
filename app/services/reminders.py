from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden

from app.config import Settings, get_settings
from app.db import get_events_collection
from app.utils.dates import (
    as_utc,
    earliest_fire,
    expire_for_repeat,
    next_schedule,
    normalize_reminders,
    safe_zoneinfo,
    to_jalali,
)
from app.utils.i18n import DEFAULT_LANGUAGE, category_label, repeat_label, t
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


def event_reminder_specs(evt: dict) -> list[dict]:
    """Reminder list for a stored event, rebuilt from the legacy hour/minute
    pair when the document predates the `reminders` field."""
    return normalize_reminders(
        evt.get("reminders"),
        all_day=bool(evt.get("all_day", True)),
        legacy_hour=evt.get("reminder_hour", 9),
        legacy_minute=evt.get("reminder_minute", 0),
    )


def event_language(evt: dict) -> str:
    return evt.get("lang") or DEFAULT_LANGUAGE


def build_reminder_text(evt: dict) -> str:
    lang = event_language(evt)
    repeat = evt.get("repeat", "none")
    date_iso = evt.get("date_iso", "")
    jalali_date = evt.get("date_jalali") or to_jalali(date_iso)
    category = evt.get("category", "general")
    pin_mark = "📌 " if evt.get("pinned") else ""
    title = html.escape(evt.get("title", ""))

    time_line = ""
    if not evt.get("all_day", True) and evt.get("time_hm"):
        time_line = f"🕒 {html.escape(str(evt['time_hm']))}\n"

    return (
        f"{t('reminder_title', lang)}\n"
        f"{pin_mark}{title}\n"
        f"📅 {date_iso}  •  {jalali_date}\n"
        f"{time_line}"
        f"🏷️ {html.escape(category_label(category, lang))}\n"
        f"🔄 {repeat_label(repeat, lang)}"
    )


def build_reminder_keyboard(evt: dict, settings: Settings) -> InlineKeyboardMarkup:
    event_id = object_id_str(evt["_id"])
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}/"
        f"{settings.telegram_mini_app_short_name}?startapp={event_id}"
    )

    lang = event_language(evt)

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("snooze_button", lang),
                    callback_data=f"snooze1h:{event_id}",
                ),
                InlineKeyboardButton(t("open_button", lang), url=deep_link),
            ]
        ]
    )


async def handle_snooze_callback(
    event_id: str,
    telegram_user_id: int | str,
    seconds: int,
) -> bool:
    try:
        oid = safe_object_id(event_id)
    except ValueError:
        return False

    # user_id is persisted as a string everywhere (see app/services/auth.py),
    # while Telegram hands us an int on callback queries. MongoDB matches on
    # the exact BSON type, so without this cast the filter never matches and
    # every snooze silently fails.
    user_id = str(telegram_user_id)

    events_coll = get_events_collection()
    new_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    result = await events_coll.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {"$set": {"next_notify_at": new_time, "notify_status": "pending"}},
    )
    return result is not None


# Each failure waits longer than the last. Before this, a failed send left
# next_notify_at untouched and in the past, so the very next pass retried
# immediately and all five attempts burned in about two minutes.
RETRY_BACKOFF_MINUTES = (1, 5, 15, 60, 180, 360)

# A send still failing after days is not transient. Capped so a genuinely
# undeliverable row cannot be retried forever.
MAX_TRANSIENT_ATTEMPTS = 20


def retry_delay(attempts: int) -> timedelta:
    index = min(max(attempts, 1), len(RETRY_BACKOFF_MINUTES)) - 1
    return timedelta(minutes=RETRY_BACKOFF_MINUTES[index])


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

        # Which occurrence this pass delivers. If an earlier pass sent it and
        # then failed to write the state change, this key is already on the
        # document and the message must not go out a second time.
        send_key = as_utc(evt["next_notify_at"]) if evt.get("next_notify_at") else None
        stored_key = claimed.get("last_sent_key")
        already_sent = (
            send_key is not None
            and stored_key is not None
            and as_utc(stored_key) == send_key
        )
        delivered = already_sent

        try:
            if already_sent:
                logger.info(
                    "event=%s already delivered; finishing the state change only",
                    evt["_id"],
                )
            else:
                await bot.send_message(
                    chat_id=evt["user_id"],
                    text=build_reminder_text(evt),
                    parse_mode="HTML",
                    reply_markup=build_reminder_keyboard(evt, settings),
                )
                delivered = True

            # The group copy comes second and in its own try: the personal
            # reminder has already been delivered, and a bot that was removed
            # from a group must not cost the owner their own reminder or stop
            # the series being rescheduled below.
            target = None if already_sent else evt.get("target_chat_id")
            if target:
                try:
                    await bot.send_message(
                        chat_id=int(target),
                        text=build_reminder_text(evt),
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.warning(
                        "group reminder failed chat=%s event=%s", target, evt["_id"]
                    )
                    try:
                        from app.services.chats import deactivate_chat

                        await deactivate_chat(int(target))
                    except Exception:
                        logger.exception("could not deactivate chat %s", target)

            repeat = evt.get("repeat", "none")
            tz, _ = safe_zoneinfo(evt.get("tz_name", "UTC"))
            occurrence = as_utc(evt.get("event_ts_utc") or now)

            if repeat != "none":
                specs = event_reminder_specs(evt)
                date_iso_raw = evt.get("date_iso", "")
                anchor_day = (
                    int(date_iso_raw[8:10]) if len(date_iso_raw) == 10 else occurrence.day
                )

                # Another reminder on the same occurrence may still be ahead.
                # Only one is exposed in the UI today, but the stored shape is a
                # list, so drain the current occurrence before moving on.
                next_occurrence = occurrence
                next_notify = earliest_fire(specs, occurrence, tz, after=now)

                if next_notify is None:
                    next_occurrence, next_notify = next_schedule(
                        occurrence_utc=occurrence,
                        anchor_day=anchor_day,
                        tz=tz,
                        reminders=specs,
                        repeat=repeat,
                        repeat_until=evt.get("repeat_until"),
                        now=now,
                    )

                if next_notify is None:
                    await events_coll.update_one(
                        {"_id": evt["_id"]},
                        {
                            "$set": {
                                "notify_status": "done",
                                "next_notify_at": None,
                                "updated_at": now,
                            },
                            "$unset": {"processing_started_at": "", "last_sent_key": ""},
                        },
                    )
                    processed += 1
                    continue

                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "pending",
                            "next_notify_at": next_notify,
                            # Moving the occurrence forward as well keeps the
                            # list sorted on the NEXT date rather than on the
                            # date the series originally started.
                            "event_ts_utc": next_occurrence,
                            "expire_at": expire_for_repeat(next_notify, repeat),
                            "notify_attempts": 0,
                            "updated_at": now,
                        },
                        "$unset": {"processing_started_at": "", "last_sent_key": ""},
                    },
                )
            else:
                await events_coll.update_one(
                    {"_id": evt["_id"]},
                    {
                        "$set": {
                            "notify_status": "done",
                            "next_notify_at": None,
                            "updated_at": now,
                        },
                        "$unset": {"processing_started_at": "", "last_sent_key": ""},
                    },
                )

            processed += 1

        except Forbidden as exc:
            # The user blocked the bot, or the chat is gone. Retrying cannot
            # change that, so this is the one genuinely terminal case.
            # health.measure() now reports it, so it is not silent.
            await events_coll.update_one(
                {"_id": evt["_id"]},
                {
                    "$set": {
                        "notify_attempts": int(evt.get("notify_attempts", 0)) + 1,
                        "notify_status": "failed",
                        "updated_at": now,
                    },
                    "$unset": {"processing_started_at": "", "last_sent_key": ""},
                },
            )
            logger.warning(
                "Reminder undeliverable for event=%s user=%s error=%s",
                evt["_id"], evt.get("user_id"), exc,
            )

        except Exception as exc:
            attempts = int(evt.get("notify_attempts", 0)) + 1
            update: dict = {
                "notify_attempts": attempts,
                "notify_status": "pending",
                "updated_at": now,
            }

            if delivered:
                # The message reached the user; only the bookkeeping failed.
                # Record which occurrence went out so the next pass finishes
                # the state change instead of sending the same reminder again,
                # and leave next_notify_at alone or the key stops matching.
                update["last_sent_key"] = send_key
            else:
                # Nothing was delivered. Back off, and only give up once the
                # failure has stopped looking transient.
                update["next_notify_at"] = now + retry_delay(attempts)
                if attempts >= MAX_TRANSIENT_ATTEMPTS:
                    update["notify_status"] = "failed"

            await events_coll.update_one(
                {"_id": evt["_id"]},
                {"$set": update, "$unset": {"processing_started_at": ""}},
            )
            logger.exception("Reminder send failed for event=%s error=%s", evt["_id"], exc)

    return processed
