from __future__ import annotations

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "fa")


def resolve_language(language_code: str | None) -> str:
    """Map a Telegram language_code onto a language this app actually speaks.

    Only Persian and English are offered, and that is deliberate rather than a
    gap: Persian serves the audience the Jalali calendar is here for, and
    English serves everyone else. A German or Spanish speaker is better off in
    English than in a language they do not read.
    """
    code = (language_code or "").strip().lower()
    return "fa" if code.startswith("fa") else DEFAULT_LANGUAGE


_STRINGS: dict[str, dict[str, str]] = {
    "start": {
        "en": (
            "👋 <b>Welcome to TimeManager Pro!</b>\n\n"
            "Save birthdays, meetings and anything else you need to remember — and get "
            "the reminder right here in Telegram, on both the Gregorian and the Jalali "
            "calendar.\n\n"
            "Tap the button below to open the app and add your first event."
        ),
        "fa": (
            "👋 <b>به تایم‌منیجر پرو خوش آمدید!</b>\n\n"
            "تولدها، جلسه‌ها و هر چیز دیگری را که می‌خواهید به یاد داشته باشید ذخیره کنید "
            "و یادآوری‌اش را همین‌جا در تلگرام بگیرید، هم به تاریخ میلادی و هم شمسی.\n\n"
            "برای باز کردن برنامه و ثبت اولین رویداد، دکمه زیر را بزنید."
        ),
    },
    "help": {
        "en": (
            "<b>TimeManager Pro — Help</b>\n\n"
            "/start — open the app and see the welcome message\n"
            "/help — show this message\n\n"
            "Everything else happens inside the Mini App: add, edit, pin and delete "
            "events, write notes, and pick the exact time you want to be reminded.\n\n"
            "When a reminder arrives, tap <b>⏰ Snooze 1h</b> to push it back an hour, "
            "or <b>📖 Open</b> to jump straight to that event."
        ),
        "fa": (
            "<b>تایم‌منیجر پرو — راهنما</b>\n\n"
            "/start — باز کردن برنامه و دیدن پیام خوش‌آمد\n"
            "/help — نمایش همین پیام\n\n"
            "بقیه کارها داخل خود برنامه انجام می‌شود: افزودن، ویرایش، سنجاق و حذف رویداد، "
            "نوشتن یادداشت، و انتخاب دقیق زمانی که می‌خواهید یادآوری شوید.\n\n"
            "وقتی یادآوری رسید، با <b>⏰ یک ساعت بعد</b> آن را یک ساعت به تعویق بیندازید "
            "یا با <b>📖 باز کردن</b> مستقیم به همان رویداد بروید."
        ),
    },
    "fallback": {
        "en": (
            "I don't understand plain text messages yet 🙂\n\n"
            "Send /help to see what I can do, or open the app with the button below."
        ),
        "fa": (
            "هنوز پیام‌های متنی معمولی را متوجه نمی‌شوم 🙂\n\n"
            "برای دیدن کارهایی که بلدم /help را بفرستید، یا با دکمه زیر برنامه را باز کنید."
        ),
    },
    "open_app_button": {"en": "📅 Open TimeManager Pro", "fa": "📅 باز کردن تایم‌منیجر پرو"},
    "snooze_button":   {"en": "⏰ Snooze 1h",            "fa": "⏰ یک ساعت بعد"},
    "open_button":     {"en": "📖 Open",                "fa": "📖 باز کردن"},
    "snoozed":  {
        "en": "⏰ Snoozed — you'll be reminded again in 1 hour.",
        "fa": "⏰ به تعویق افتاد — یک ساعت دیگر دوباره یادآوری می‌شود.",
    },
    "snooze_failed": {
        "en": "Couldn't snooze that event.",
        "fa": "به تعویق انداختن این رویداد ممکن نشد.",
    },
    "unknown_action": {"en": "Unknown action.", "fa": "این دستور شناخته نشد."},
    "card_days_left": {"en": "days left", "fa": "روز مانده"},
    "card_days_ago": {"en": "days ago", "fa": "روز گذشته"},
    "card_today": {"en": "Today", "fa": "امروز"},
    "card_gregorian": {"en": "Gregorian", "fa": "میلادی"},
    "card_jalali": {"en": "Jalali", "fa": "شمسی"},
    "card_time": {"en": "Time", "fa": "ساعت"},
    "share_caption": {
        "en": "<b>{title}</b> — counting down with TimeManager Pro.",
        "fa": "<b>{title}</b> — شمارش معکوس با تایم‌منیجر پرو.",
    },
    "share_button": {"en": "Open in TimeManager", "fa": "باز کردن در تایم‌منیجر"},
    "share_open_button": {"en": "Open in Telegram", "fa": "باز کردن در تلگرام"},
    "chat_connected": {
        "en": (
            "✅ <b>{title}</b> is connected.\n\n"
            "When you create an event you can now send its reminder there as "
            "well as here. Anyone else in that chat can add it to their own "
            "list with this link:\n{link}"
        ),
        "fa": (
            "✅ <b>{title}</b> وصل شد.\n\n"
            "از این به بعد موقع ساخت رویداد می‌توانی یادآوری‌اش را علاوه بر "
            "اینجا، آنجا هم بفرستی. هر کس دیگری در آن چت با این لینک می‌تواند "
            "به فهرست خودش اضافه‌اش کند:\n{link}"
        ),
    },
    "share_join_button": {
        "en": "Add to my events",
        "fa": "به رویدادهای من اضافه کن",
    },
    "share_made_with": {
        "en": "Made with TimeManager Pro",
        "fa": "ساخته‌شده با تایم‌منیجر پرو",
    },
    "referral_bonus_granted": {
        "en": (
            "🎁 <b>Your limit just went up!</b>\n\n"
            "Someone you invited saved their first event. "
            "You can now keep up to <b>{limit}</b> events."
        ),
        "fa": (
            "🎁 <b>سقف شما بالا رفت!</b>\n\n"
            "کسی که دعوت کرده بودید اولین رویدادش را ذخیره کرد. "
            "حالا می‌توانید تا <b>{limit}</b> رویداد داشته باشید."
        ),
    },
    "reminder_title": {"en": "🔔 <b>Reminder</b>", "fa": "🔔 <b>یادآوری</b>"},
    "label_category": {"en": "🏷️", "fa": "🏷️"},
    "repeat_none":    {"en": "One-time",  "fa": "یک‌بار"},
    "repeat_daily":   {"en": "🔁 Daily",   "fa": "🔁 هر روز"},
    "repeat_weekly":  {"en": "🔁 Weekly",  "fa": "🔁 هر هفته"},
    "repeat_monthly": {"en": "🔁 Monthly", "fa": "🔁 هر ماه"},
    "repeat_yearly":  {"en": "🎂 Yearly",  "fa": "🎂 هر سال"},
    "category_general":  {"en": "General",  "fa": "عمومی"},
    "category_birthday": {"en": "Birthday", "fa": "تولد"},
    "category_work":     {"en": "Work",     "fa": "کاری"},
    "category_family":   {"en": "Family",   "fa": "خانواده"},
    "category_health":   {"en": "Health",   "fa": "سلامت"},
    "category_travel":   {"en": "Travel",   "fa": "سفر"},
    "category_finance":  {"en": "Finance",  "fa": "مالی"},
    "category_study":    {"en": "Study",    "fa": "درسی"},
    "category_other":    {"en": "Other",    "fa": "سایر"},
}


def t(key: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Look up a string, falling back to English and then to the key itself."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(language) or entry.get(DEFAULT_LANGUAGE, key)


def repeat_label(repeat: str, language: str = DEFAULT_LANGUAGE) -> str:
    return t(f"repeat_{repeat}", language) if f"repeat_{repeat}" in _STRINGS else t("repeat_none", language)


def category_label(category: str, language: str = DEFAULT_LANGUAGE) -> str:
    key = f"category_{category}"
    return t(key, language) if key in _STRINGS else t("category_general", language)
