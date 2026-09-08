/**
 * TimeManager Pro — app.js v2.0
 * Fixes applied:
 *  - event_id (was: eventid) in edit/delete/pin/note payloads
 *  - All countdown text translated to English (was: Persian)
 *  - window.confirm replaced with custom confirm dialog
 *  - All debug console.log removed
 *  - API field names: date_iso / date_jalali / notify_status / tz_name
 *  - Pinned badge text: "Pinned" (was: "سنجاق‌شده")
 *  - Skeleton loading state
 *  - Pagination / load-more
 *  - Countdown ring in detail view
 *  - Note character counter
 *  - Reminder hour field support
 *  - Reminder hour actually sent to the backend (was: silently dropped)
 *  - Haptic feedback on save/delete/pin/error (via Telegram WebApp SDK)
 *  - First-run onboarding overlay (3 steps, shown once via localStorage)
 */

(() => {
  "use strict";

  /* ── Telegram WebApp ────────────────────────────────── */
  const tg = window.Telegram?.WebApp || null;

  function fatal(message) {
    document.body.innerHTML = `
      <div style="padding:40px 20px;text-align:center;font-family:system-ui,sans-serif;">
        <div style="font-size:2.5rem;margin-bottom:16px;">⚠️</div>
        <h2 style="margin:0 0 12px;font-size:1.2rem;">Something went wrong</h2>
        <p style="color:#666;margin:0;">${String(message).replace(/</g, "&lt;")}</p>
      </div>
    `;
  }

  if (!tg) {
    fatal("This application only works inside Telegram. Please open it via the Telegram Mini App.");
    return;
  }

  try { tg.ready(); tg.expand(); } catch (_) {}

  const initData = tg.initData || "";

  if (!initData) {
    fatal("Telegram Mini App could not authenticate. Please reopen the app from Telegram.");
    return;
  }

  /* ── App State ──────────────────────────────────────── */
  const state = {
    events: [],
    filteredEvents: [],
    currentFilter: "all",
    searchTerm: "",
    activeSheet: null,
    detailEventId: null,
    editingEventId: null,
    lastFocusedElement: null,
    skip: 0,
    hasMore: false,
    isLoading: false,
    initData,
  };

  /* ── Element Refs ───────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const els = {
    syncStatus:         $("syncStatus"),
    eventCount:         $("eventCount"),
    refreshBtn:         $("refreshBtn"),
    retryBtn:           $("retryBtn"),
    emptyAddBtn:        $("emptyAddBtn"),
    onboardingOverlay:  $("onboardingOverlay"),
    onboardingIcon:     $("onboardingIcon"),
    onboardingTitle:    $("onboardingTitle"),
    onboardingText:     $("onboardingText"),
    onboardingDots:     $("onboardingDots"),
    onboardingSkipBtn:  $("onboardingSkipBtn"),
    onboardingNextBtn:  $("onboardingNextBtn"),
    repeatUntilWrap:    $("repeatUntilWrap"),
    repeatUntil:        $("repeatUntil"),
    searchInput:        $("searchInput"),
    filterButtons:      $$("[data-filter]"),
    eventsWrap:         $("eventsWrap"),
    listState:          $("listState"),
    listErrorState:     $("listErrorState"),
    noResultsState:     $("noResultsState"),
    skeletonState:      $("skeletonState"),
    loadMoreWrap:       $("loadMoreWrap"),
    loadMoreBtn:        $("loadMoreBtn"),
    toast:              $("toast"),

    // Composer
    openComposerBtn:    $("openComposerBtn"),
    closeComposerX:     $("closeComposerX"),
    cancelBtn:          $("cancelBtn"),
    saveEventBtn:       $("saveEventBtn"),
    composerSheet:      $("composerSheet"),
    composerTitle:      $("composerTitle"),
    composerSubtitle:   $("composerSubtitle"),
    eventForm:          $("eventForm"),
    eventId:            $("eventId"),
    title:              $("title"),
    date:               $("date"),
    dateJalali:         $("date-jalali"),
    repeat:             $("repeat"),
    category:           $("category"),
    pin:                $("pin"),
    note:               $("note"),
    noteCharCount:      $("noteCharCount"),
    allDay:             $("allDay"),
    eventTimeWrap:      $("eventTimeWrap"),
    eventTime:          $("eventTime"),
    reminderTimeWrap:   $("reminderTimeWrap"),
    reminderTime:       $("reminderTime"),
    reminderOffsetWrap: $("reminderOffsetWrap"),
    reminderOffset:     $("reminderOffset"),
    dpOverlay:          $("dpOverlay"),
    dpTabs:             Array.from(document.querySelectorAll(".dp-tab")),
    dpYear:             $("dpYear"),
    dpMonth:            $("dpMonth"),
    dpDay:              $("dpDay"),
    dpPreview:          $("dpPreview"),
    dpToday:            $("dpToday"),
    dpClear:            $("dpClear"),
    dpCancel:           $("dpCancel"),
    dpConfirm:          $("dpConfirm"),

    // Detail
    detailSheet:        $("detailSheet"),
    closeDetailX:       $("closeDetailX"),
    detailEditBtn:      $("detailEditBtn"),
    detailShareBtn:     $("detailShareBtn"),
    detailPinBtn:       $("detailPinBtn"),
    detailDeleteBtn:    $("detailDeleteBtn"),
    detailNote:         $("detailNote"),
    detailNoteSaveBtn:  $("detailNoteSaveBtn"),
    detailNoteCancelBtn:$("detailNoteCancelBtn"),
    detailEventTitle:   $("detailEventTitle"),
    detailCategoryBadge:$("detailCategoryBadge"),
    detailRepeatBadge:  $("detailRepeatBadge"),
    detailPinnedBadge:  $("detailPinnedBadge"),
    detailDateIso:      $("detailDateIso"),
    detailDateJalali:   $("detailDateJalali"),
    detailTimezone:     $("detailTimezone"),
    detailStatus:       $("detailStatus"),
    countdownRing:      $("countdownRing"),
    countdownDays:      $("countdownDays"),
    detailCountdownText:$("detailCountdownText"),

    // Confirm dialog
    confirmOverlay:     $("confirmOverlay"),
    confirmTitle:       $("confirmTitle"),
    confirmText:        $("confirmText"),
    confirmOkBtn:       $("confirmOkBtn"),
    confirmCancelBtn:   $("confirmCancelBtn"),

    sheetOverlay:       $("sheetOverlay"),
  };

  /* ── Label Maps ─────────────────────────────────────── */
  const CATEGORY_LABELS = {
    general: "🌐 General",  birthday: "🎂 Birthday",
    work:    "💼 Work",      family:   "👨‍👩‍👧 Family",
    health:  "❤️ Health",   travel:   "✈️ Travel",
    finance: "💰 Finance",  study:    "📚 Study",
    other:   "📌 Other",
  };

  /* ── Language ────────────────────────────────────────── */
  // Keyed by the English source string rather than by an invented id: the
  // template needs no data-i18n attributes, so translating it is a DOM pass
  // instead of 99 markup edits, and any string with no entry simply stays
  // English. Only Persian is offered besides English — that is deliberate, not
  // a gap: Persian serves the audience the Jalali calendar is here for, and
  // English serves everyone else.
  const TRANSLATIONS = {
    fa: {
      // Shell
      "Skip to content": "پرش به محتوا",
      "JavaScript Required": "جاوااسکریپت لازم است",
      "Please enable JavaScript to use TimeManager Pro.": "برای استفاده از تایم‌منیجر پرو جاوااسکریپت را فعال کنید.",
      "Smart reminders in Telegram": "یادآوری هوشمند در تلگرام",
      "✨ Your Personal Planner": "✨ برنامه‌ریز شخصی شما",
      "Stay on top of every moment": "هیچ لحظه‌ای را از دست ندهید",
      "Save events, birthdays & tasks — get reminders directly in Telegram.": "رویدادها، تولدها و کارها را ذخیره کنید و یادآوری‌شان را در تلگرام بگیرید.",
      "Events": "رویداد",
      "Ready": "آماده",
      "Status": "وضعیت",
      "Refresh events": "بارگذاری دوباره",
      "Refresh": "بارگذاری دوباره",

      // Filters and search
      "Filters and search": "فیلتر و جست‌وجو",
      "Category filter": "فیلتر دسته",
      "Search events…": "جست‌وجوی رویداد…",
      "Event list": "فهرست رویدادها",
      "🌐 All": "🌐 همه",
      "📌 Pinned": "📌 سنجاق‌شده",
      "🎂 Birthday": "🎂 تولد",
      "💼 Work": "💼 کاری",
      "❤️ Health": "❤️ سلامت",
      "👨‍👩‍👧 Family": "👨‍👩‍👧 خانواده",
      "✈️ Travel": "✈️ سفر",
      "💰 Finance": "💰 مالی",
      "📚 Study": "📚 درسی",
      "🗄️ Past": "🗄️ گذشته",

      // Date picker
      "Pick a date": "انتخاب تاریخ",
      "Calendar": "تقویم",
      "Gregorian": "میلادی",
      "Jalali": "شمسی",
      "Year": "سال",
      "Month": "ماه",
      "Day": "روز",
      "Today": "امروز",
      "Clear": "پاک کردن",
      "Confirm": "تأیید",
      "January": "ژانویه", "February": "فوریه", "March": "مارس",
      "April": "آوریل", "May": "مه", "June": "ژوئن",
      "July": "ژوئیه", "August": "اوت", "September": "سپتامبر",
      "October": "اکتبر", "November": "نوامبر", "December": "دسامبر",
      "Farvardin": "فروردین", "Ordibehesht": "اردیبهشت", "Khordad": "خرداد",
      "Tir": "تیر", "Mordad": "مرداد", "Shahrivar": "شهریور",
      "Mehr": "مهر", "Aban": "آبان", "Azar": "آذر",
      "Dey": "دی", "Bahman": "بهمن", "Esfand": "اسفند",
      "🌐 General": "🌐 عمومی",
      "📌 Other": "📌 سایر",

      // Empty, error and result states
      "No events yet!": "هنوز رویدادی ندارید!",
      "Add your first event and start receiving smart reminders directly in Telegram.": "اولین رویدادتان را اضافه کنید و یادآوری‌ها را در تلگرام دریافت کنید.",
      "Add your first event": "افزودن اولین رویداد",
      "🎂 Birthdays": "🎂 تولدها",
      "💼 Meetings": "💼 جلسه‌ها",
      "❤️ Appointments": "❤️ قرارها",
      "✈️ Travel": "✈️ سفر",
      "Something went wrong": "مشکلی پیش آمد",
      "Could not connect to the server. Please check your connection and try again.": "اتصال به سرور ممکن نشد. اینترنت را بررسی و دوباره تلاش کنید.",
      "Try again": "تلاش دوباره",
      "No results found": "چیزی پیدا نشد",
      "Try a different search term or filter.": "عبارت یا فیلتر دیگری را امتحان کنید.",
      "Load more events": "رویدادهای بیشتر",

      // Composer
      "Add event": "افزودن رویداد",
      "Add Event": "افزودن رویداد",
      "New Event": "رویداد جدید",
      "Edit Event": "ویرایش رویداد",
      "Close form": "بستن فرم",
      "Set title, date and repeat pattern.": "عنوان، تاریخ و الگوی تکرار را مشخص کنید.",
      "Event Title": "عنوان رویداد",
      "e.g. Mom's Birthday": "مثلاً تولد مامان",
      "Gregorian Date": "تاریخ میلادی",
      "Jalali Date": "تاریخ شمسی",
      "Repeat": "تکرار",
      "One time": "یک‌بار",
      "Daily": "هر روز",
      "Weekly": "هر هفته",
      "Monthly": "هر ماه",
      "Yearly": "هر سال",
      "Repeat Until": "تکرار تا",
      "(optional)": "(اختیاری)",
      "Category": "دسته",
      "All-day event": "رویداد تمام‌روز",
      "Event Time": "ساعت رویداد",
      "Reminder Time": "ساعت یادآوری",
      "Remind Me": "یادآوری",
      "At time of event": "سر ساعت رویداد",
      "15 minutes before": "۱۵ دقیقه قبل",
      "30 minutes before": "۳۰ دقیقه قبل",
      "1 hour before": "۱ ساعت قبل",
      "2 hours before": "۲ ساعت قبل",
      "1 day before": "۱ روز قبل",
      "1 week before": "۱ هفته قبل",
      "Pin this event to the top": "این رویداد بالای فهرست بماند",
      "Note": "یادداشت",
      "Add details, tasks, or a checklist…": "جزئیات، کارها یا فهرست وارسی…",
      "Cancel": "انصراف",
      "Save Event": "ذخیره رویداد",
      "Save Changes": "ذخیره تغییرات",

      // Detail sheet
      "Event Details": "جزئیات رویداد",
      "Full view and actions": "نمای کامل و عملیات",
      "Close details": "بستن جزئیات",
      "days": "روز",
      "📅 Gregorian": "📅 میلادی",
      "🗓️ Jalali": "🗓️ شمسی",
      "🌍 Timezone": "🌍 منطقه زمانی",
      "🔔 Status": "🔔 وضعیت",
      "Edit": "ویرایش",
      "Share": "اشتراک",
      "📌 Pin": "📌 سنجاق",
      "Delete": "حذف",
      "Event Note": "یادداشت رویداد",
      "Write a note, checklist, or details…": "یادداشت، فهرست وارسی یا جزئیات…",
      "Reset": "بازنشانی",
      "Save Note": "ذخیره یادداشت",
      "Delete Event?": "رویداد حذف شود؟",
      "This action cannot be undone.": "این کار قابل بازگشت نیست.",

      // Onboarding
      "Never miss what matters": "هیچ چیز مهمی را فراموش نکنید",
      "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.": "تولدها، قرارها و هر چیز دیگری را که می‌خواهید به یاد بماند اضافه کنید — تایم‌منیجر پرو به جای شما یادش می‌ماند.",
      "Reminders come straight to Telegram": "یادآوری‌ها مستقیم به تلگرام می‌رسند",
      "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.": "لازم نیست برنامه دیگری را چک کنید. سر وقتش همین‌جا پیام می‌گیرید، یک‌بار یا با تکراری که خودتان انتخاب می‌کنید.",
      "Gregorian & Jalali, together": "میلادی و شمسی، کنار هم",
      "Every date shows in both calendars automatically. Tap the + button below to add your first event.": "هر تاریخ خودکار در هر دو تقویم نشان داده می‌شود. برای افزودن اولین رویداد دکمه + را بزنید.",
      "Skip": "رد کردن",
      "Next": "بعدی",
      "Get Started": "شروع کنیم",

      // Categories and statuses rendered from JS
      "General": "عمومی",
      "Birthday": "تولد",
      "Work": "کاری",
      "Family": "خانواده",
      "Health": "سلامت",
      "Travel": "سفر",
      "Finance": "مالی",
      "Study": "درسی",
      "Other": "سایر",
      "Pinned": "سنجاق‌شده",
      "Pending": "در انتظار",
      "Processing...": "در حال ارسال…",
      "✅ Sent": "✅ ارسال شد",
      "❌ Failed": "❌ ناموفق",

      // Toasts and errors
      "Please enter an event title.": "لطفاً عنوان رویداد را وارد کنید.",
      "Please select a date.": "لطفاً تاریخ را انتخاب کنید.",
      "Please set the event time, or mark it as an all-day event.": "ساعت رویداد را مشخص کنید یا آن را تمام‌روز علامت بزنید.",
      "Event saved! You'll receive a reminder in Telegram.": "رویداد ذخیره شد! یادآوری‌اش در تلگرام می‌رسد.",
      "Event updated successfully.": "رویداد به‌روزرسانی شد.",
      "Event deleted.": "رویداد حذف شد.",
      "Note saved.": "یادداشت ذخیره شد.",
      "Event pinned to top.": "رویداد بالای فهرست سنجاق شد.",
      "Event unpinned.": "سنجاق رویداد برداشته شد.",
      "Shared!": "به اشتراک گذاشته شد!",
      "Event details copied to clipboard.": "جزئیات رویداد کپی شد.",
      "Could not share. Please try copying manually.": "اشتراک‌گذاری ممکن نشد. دستی کپی کنید.",
      "The note is too long (max 2000 chars).": "یادداشت خیلی بلند است (حداکثر ۲۰۰۰ نویسه).",
      "You have reached your event limit. Invite friends to raise it.": "به سقف رویدادهایتان رسیده‌اید. با دعوت دوستان آن را بالا ببرید.",
      "Too many requests. Please slow down.": "درخواست‌ها زیاد است. کمی آهسته‌تر.",
      "Event not found or access denied.": "رویداد پیدا نشد یا دسترسی ندارید.",
      "The request failed. Please try again.": "درخواست ناموفق بود. دوباره تلاش کنید.",
      "Telegram authentication data is incomplete.": "اطلاعات احراز هویت تلگرام ناقص است.",
      "User information was not received from Telegram.": "اطلاعات کاربر از تلگرام دریافت نشد.",
      "Authentication timestamp is invalid.": "زمان احراز هویت معتبر نیست.",
      "Server configuration error. Please contact support.": "خطای پیکربندی سرور. با پشتیبانی تماس بگیرید.",
      "Invalid event ID.": "شناسه رویداد نامعتبر است.",

      // Countdown
      "Today! 🎉": "امروز! 🎉",
      "This event is today!": "این رویداد امروز است!",
      "{days} ago": "{days} پیش",
      "This event was {days} ago": "{days} پیش بوده است",
      "{parts} remaining": "{parts} مانده",
      "{parts} left": "{parts} مانده",
      "day": "روز",
      "week": "هفته",
      "month": "ماه",
      "year": "سال",
      "yr": "سال",
      "mo": "ماه",
    },
  };

  let currentLang = "en";

  function t(text, vars) {
    const table = TRANSLATIONS[currentLang] || {};
    let out = table[text] || text;
    if (vars) {
      Object.keys(vars).forEach((key) => {
        out = out.split(`{${key}}`).join(vars[key]);
      });
    }
    return out;
  }

  // Walks the static markup once and swaps any text node or attribute whose
  // trimmed value has an entry. Unknown strings are left alone, so a missing
  // translation degrades to English rather than to a blank.
  function translateDocument(root) {
    root = root || document.body;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const parent = node.parentElement;
        if (!parent || parent.closest("script, style")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach((node) => {
      const trimmed = node.nodeValue.trim();
      const translated = t(trimmed);
      if (translated !== trimmed) node.nodeValue = node.nodeValue.replace(trimmed, translated);
    });

    ["placeholder", "aria-label", "title"].forEach((attr) => {
      root.querySelectorAll(`[${attr}]`).forEach((el) => {
        const value = (el.getAttribute(attr) || "").trim();
        const translated = t(value);
        if (translated !== value) el.setAttribute(attr, translated);
      });
    });
  }

  function applyLanguage() {
    const code = String(tg?.initDataUnsafe?.user?.language_code || "").toLowerCase();
    currentLang = code.startsWith("fa") ? "fa" : "en";

    const root = document.documentElement;
    root.lang = currentLang;
    root.dir = currentLang === "fa" ? "rtl" : "ltr";

    if (currentLang !== "en") translateDocument();
  }

  const CATEGORY_PLAIN = {
    general: "General",  birthday: "Birthday",
    work:    "Work",      family:   "Family",
    health:  "Health",   travel:   "Travel",
    finance: "Finance",  study:    "Study",
    other:   "Other",
  };

  const REPEAT_LABELS = {
    none: "One time", daily: "🔁 Daily",
    weekly: "🔁 Weekly", monthly: "🔁 Monthly", yearly: "🎂 Yearly",
  };

  const STATUS_LABELS = {
    pending: "Pending", processing: "Processing...",
    done: "✅ Sent", failed: "❌ Failed",
  };

  /* ── Telegram Theme ─────────────────────────────────── */
  // Telegram themeParams key -> CSS custom property read by style.css.
  // Every one of these has a fallback in the stylesheet, so a client that
  // sends only half of them still renders correctly.
  const TG_THEME_MAP = {
    bg_color:                "--tg-bg",
    secondary_bg_color:      "--tg-bg-2",
    section_bg_color:        "--tg-surface",
    text_color:              "--tg-text",
    subtitle_text_color:     "--tg-text-2",
    hint_color:              "--tg-text-muted",
    section_separator_color: "--tg-border",
    link_color:              "--tg-link",
    destructive_text_color:  "--tg-danger",
  };

  function initTelegram() {
    try {
      applyTelegramTheme();
      if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("secondary_bg_color");
      tg.onEvent?.("themeChanged", applyTelegramTheme);
    } catch (_) {}
  }

  function applyTelegramTheme() {
    const root = document.documentElement;
    const params = tg?.themeParams || {};

    Object.entries(TG_THEME_MAP).forEach(([key, cssVar]) => {
      const value = params[key];
      if (typeof value === "string" && value.trim()) {
        root.style.setProperty(cssVar, value.trim());
      } else {
        // Client didn't send this one — drop back to the stylesheet default
        // instead of keeping a stale value from the previous theme.
        root.style.removeProperty(cssVar);
      }
    });

    // Inside the Telegram WebView tg.colorScheme is authoritative:
    // prefers-color-scheme reports the OS setting, which can disagree with
    // the theme the user actually chose in Telegram.
    root.setAttribute("data-tg-scheme", tg?.colorScheme === "dark" ? "dark" : "light");
  }

  /* ── Loading / Status ───────────────────────────────── */
  function setLoading(on) {
    state.isLoading = on;
    document.body.classList.toggle("is-loading", on);
    if (els.syncStatus) els.syncStatus.textContent = on ? "Syncing…" : "Ready";
  }

  function setSkeleton(on) {
    if (els.skeletonState) els.skeletonState.hidden = !on;
  }

  /* ── Toast ──────────────────────────────────────────── */
  let _toastTimer = null;
  function showToast(message, type = "info") {
    if (!els.toast) return;
    els.toast.innerHTML = `
      ${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"} ${escapeHtml(message)}
    `;
    els.toast.dataset.type = type;
    els.toast.classList.add("is-visible");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => els.toast.classList.remove("is-visible"), 2800);

    // Native-feeling haptic nudge on meaningful outcomes (skip routine "info" toasts
    // so this stays purposeful rather than buzzing on everything).
    try {
      if (type === "success") tg?.HapticFeedback?.notificationOccurred?.("success");
      else if (type === "error") tg?.HapticFeedback?.notificationOccurred?.("error");
    } catch (_) {}
  }

  /* ── Custom Confirm Dialog ──────────────────────────── */
  function showConfirm({ title, text, okLabel = "Confirm", icon = "🗑️" }) {
    return new Promise((resolve) => {
      if (!els.confirmOverlay) { resolve(true); return; }

      if (els.confirmTitle) els.confirmTitle.textContent = title;
      if (els.confirmText)  els.confirmText.textContent  = text;
      if (els.confirmOkBtn) els.confirmOkBtn.textContent = okLabel;
      const iconEl = els.confirmOverlay.querySelector(".confirm-icon");
      if (iconEl) iconEl.textContent = icon;

      els.confirmOverlay.hidden = false;
      els.confirmOverlay.removeAttribute("aria-hidden");

      const cleanup = (result) => {
        els.confirmOverlay.hidden = true;
        els.confirmOverlay.setAttribute("aria-hidden", "true");
        resolve(result);
      };

      const handleOk     = () => cleanup(true);
      const handleCancel = () => cleanup(false);
      const handleKey    = (e) => { if (e.key === "Escape") cleanup(false); };

      els.confirmOkBtn?.addEventListener("click", handleOk, { once: true });
      els.confirmCancelBtn?.addEventListener("click", handleCancel, { once: true });
      document.addEventListener("keydown", handleKey, { once: true });
    });
  }

  /* ── API ────────────────────────────────────────────── */
  async function apiPost(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: state.initData, ...payload }),
    });

    let data = null;
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      const detail = data?.detail || "REQUEST_FAILED";
      throw new Error(detail);
    }
    return data;
  }

  function normalizeError(error) {
    const map = {
      NO_DATA:                "Telegram authentication data is missing.",
      BAD_HASH:               "The Telegram request signature is invalid.",
      EXPIRED:                "Your session has expired. Please reopen the Mini App.",
      INVALID_DATE:           "The date entered is not valid.",
      TITLE_TOO_LONG:         "The title is too long (max 200 chars).",
      NOTE_TOO_LONG:          "The note is too long (max 2000 chars).",
      EVENT_LIMIT_REACHED:    "You have reached your event limit. Invite friends to raise it.",
      RATE_LIMIT:             "Too many requests. Please slow down.",
      NOT_FOUND_OR_UNAUTHORIZED: "Event not found or access denied.",
      REQUEST_FAILED:         "The request failed. Please try again.",
      NO_HASH:                "Telegram authentication data is incomplete.",
      NO_USER:                "User information was not received from Telegram.",
      INVALID_AUTH_DATE:      "Authentication timestamp is invalid.",
      MISCONFIGURED:          "Server configuration error. Please contact support.",
      INVALID_ID_FORMAT:      "Invalid event ID.",
    };
    const detail = error?.message || "";
    return t(map[detail]) || `Error: ${detail || "Unknown error"}`;
  }

  /* ── Load Events ─────────────────────────────────────── */
  // Long enough that a normal typing burst is one request, short enough that
  // the list still feels like it reacts as you type.
  const SEARCH_DEBOUNCE_MS = 350;

  async function loadEvents(append = false) {
    if (!append) {
      state.skip = 0;

      // The skeleton only stands in for an empty list. Clearing the cards
      // before the response arrives threw away the very nodes the reconciler
      // animates from, and made every filter change flash empty on the way.
      setSkeleton(!els.eventsWrap || !els.eventsWrap.children.length);
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = true;
      if (els.noResultsState) els.noResultsState.hidden = true;
      if (els.loadMoreWrap) els.loadMoreWrap.hidden = true;
    }

    setLoading(true);
    try {
      const data = await apiPost("/api/list", {
        skip:   state.skip,
        q:      state.searchTerm.trim(),
        filter: state.currentFilter,
      });
      const newItems = Array.isArray(data.targets) ? data.targets : [];
      state.hasMore = !!data.has_more;

      if (append) {
        state.events = [...state.events, ...newItems];
      } else {
        state.events = newItems;
      }

      state.skip = state.events.length;
      applyFilters();
      renderEvents();
      updateCounters();
      showStatePanel();

      if (els.loadMoreWrap) els.loadMoreWrap.hidden = !state.hasMore;
    } catch (error) {
      state.events = [];
      state.filteredEvents = [];
      clearCards();
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = false;
      showToast(normalizeError(error), "error");
      if (els.syncStatus) els.syncStatus.textContent = "Error";
    } finally {
      setSkeleton(false);
      setLoading(false);
    }
  }

  function updateCounters() {
    if (els.eventCount) els.eventCount.textContent = String(state.events.length);
  }

  /* ── Filters ────────────────────────────────────────── */
  // The search and the filter are applied by the Mongo query behind /api/list,
  // so what comes back is already the result set. Filtering again here would
  // only ever narrow it to the current page, which is the bug this replaced.
  function applyFilters() {
    state.filteredEvents = state.events;
  }

  /* ── State Panel ────────────────────────────────────── */
  function showStatePanel() {
    const isSearching = state.searchTerm.trim() !== "" || state.currentFilter !== "all";
    const hasResults  = state.filteredEvents.length > 0;

    if (els.listState)      els.listState.hidden      = true;
    if (els.listErrorState) els.listErrorState.hidden = true;
    if (els.noResultsState) els.noResultsState.hidden = true;

    if (hasResults) return;

    // Now that the server does the filtering, an empty page means one of two
    // different things: nothing matched the query, or nothing is saved at all.
    // Comparing events to filteredEvents can no longer tell them apart.
    if (isSearching) {
      if (els.noResultsState) els.noResultsState.hidden = false;
    } else if (els.listState) {
      els.listState.hidden = false;
    }
  }

  /* ── Countdown Logic (English only) ─────────────────── */
  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addMonthsSafe(date, n) {
    const d = new Date(date.getFullYear(), date.getMonth(), 1);
    d.setMonth(d.getMonth() + n);
    const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(date.getDate(), lastDay));
    return d;
  }

  function diffParts(from, to) {
    let cursor = startOfDay(from);
    const target = startOfDay(to);
    const totalMs = target - cursor;

    if (totalMs < 0) {
      return { past: true, totalDays: Math.ceil(-totalMs / 86400000) };
    }

    const totalDays = Math.ceil(totalMs / 86400000);

    let years = 0, months = 0;
    while (addMonthsSafe(cursor, 12) <= target) { years++;  cursor = addMonthsSafe(cursor, 12); }
    while (addMonthsSafe(cursor, 1)  <= target) { months++; cursor = addMonthsSafe(cursor, 1); }

    const remDays = Math.ceil((target - cursor) / 86400000);
    const weeks = Math.floor(remDays / 7);
    const days  = remDays % 7;

    return { past: false, years, months, weeks, days, totalDays };
  }

  function pluralize(n, word) {
    // Persian marks no plural on a counted noun: "3 days" is "۳ روز", not "روزها".
    if (currentLang !== "en") return `${n} ${t(word)}`;
    return `${n} ${word}${n !== 1 ? "s" : ""}`;
  }

  function getCountdownData(dateIso) {
    if (!dateIso) return { tone: "long", shortText: "—", fullText: "—", totalDays: 999 };

    const today = startOfDay(new Date());
    const target = startOfDay(new Date(`${dateIso}T00:00:00`));
    const diff = diffParts(today, target);

    if (diff.past) {
      return {
        tone: "past",
        shortText: t("{days} ago", { days: pluralize(diff.totalDays, "day") }),
        fullText: t("This event was {days} ago", { days: pluralize(diff.totalDays, "day") }),
        totalDays: -diff.totalDays,
      };
    }

    if (diff.totalDays === 0) {
      return {
        tone: "today",
        shortText: t("Today! 🎉"),
        fullText: t("This event is today!"),
        totalDays: 0,
      };
    }

    // Build human-readable parts
    const parts = [];
    if (diff.years)  parts.push(pluralize(diff.years, "year"));
    if (diff.months) parts.push(pluralize(diff.months, "month"));
    if (diff.weeks)  parts.push(pluralize(diff.weeks, "week"));
    if (diff.days)   parts.push(pluralize(diff.days, "day"));

    const shortParts = [];
    if (diff.years)  shortParts.push(pluralize(diff.years, "yr"));
    if (diff.months) shortParts.push(pluralize(diff.months, "mo"));
    const extraDays = diff.weeks * 7 + diff.days;
    if (extraDays)   shortParts.push(pluralize(extraDays, "day"));

    const fullText  = t("{parts} remaining", { parts: parts.join("، ") });
    const shortText = t("{parts} left",       { parts: shortParts.join(" ") });

    let tone = "long";
    // Tomorrow is its own tone rather than the top of the critical bucket: the
    // list groups by it and it has to read louder than "in a week". The old
    // <=3 and <=7 branches both returned "critical", so the second could never
    // be reached — they are one branch now.
    if      (diff.totalDays === 1)  tone = "tomorrow";
    else if (diff.totalDays <= 7)   tone = "critical";
    else if (diff.totalDays <= 30)  tone = "soon";
    else if (diff.totalDays <= 90)  tone = "warm";
    else if (diff.totalDays <= 180) tone = "cool";
    else if (diff.totalDays <= 365) tone = "future";

    return { tone, shortText, fullText, totalDays: diff.totalDays };
  }

  /* ── Render Events ───────────────────────────────────── */
  // Reconciled against the DOM by id rather than rebuilt. The old version
  // emptied the wrapper and recreated every article on each render, which
  // dropped keyboard focus, re-bound every listener, and — worst of all — left
  // the browser with no way to know that a card had moved rather than been
  // replaced. Nothing could be animated because nothing survived.

  const cardIndex = new Map();      // event id -> <article>
  const headerIndex = new Map();    // section key -> <div>

  const SECTION_LABELS = {
    pinned: "Pinned",
    today: "Today",
    tomorrow: "Tomorrow",
    later: "Later",
  };

  const CARD_ICONS = {
    pin: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    moon: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    bell: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
  };

  function prefersReducedMotion() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }

  function clearCards() {
    if (els.eventsWrap) els.eventsWrap.replaceChildren();
    cardIndex.clear();
    headerIndex.clear();
    openRow = null;
  }

  /* ── Swipe actions ───────────────────────────────────
     Reveal, not commit. Dragging uncovers the buttons and the button does the
     work. In a list whose main gesture is a vertical scroll, a one-step swipe
     that fires on release gets triggered by accident far too often — and the
     one action here that cannot be undone is delete.

     Nothing below needs to know about right-to-left. The card follows the
     finger physically, and the two action strips are placed with
     inset-inline-start/end, so Persian flips them for free. */

  const SWIPE_WIDTH = 84;     // how far a row opens
  const SWIPE_TRIGGER = 40;   // drag past this and it stays open
  const SWIPE_SLOP = 8;       // ignore the first few pixels of any gesture
  let openRow = null;

  const ROW_ICONS = {
    pin: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>',
  };

  function rowActionButton(kind, icon, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `row-action row-action-${kind}`;
    // Behind a closed card these would otherwise sit in the tab order of every
    // row. The detail sheet already offers both actions to keyboard users.
    button.tabIndex = -1;
    button.innerHTML = `${icon}<span class="row-action-label">${label}</span>`;
    return button;
  }

  function wrapInRow(art, id) {
    const row = document.createElement("div");
    row.className = "event-row";
    row.dataset.id = id;

    const startSide = document.createElement("div");
    startSide.className = "event-actions event-actions-start";
    const pinBtn = rowActionButton("pin", ROW_ICONS.pin, t("Pin"));
    startSide.appendChild(pinBtn);

    const endSide = document.createElement("div");
    endSide.className = "event-actions event-actions-end";
    const deleteBtn = rowActionButton("delete", ROW_ICONS.trash, t("Delete"));
    endSide.appendChild(deleteBtn);

    row.append(startSide, endSide, art);
    row._card = art;
    row._pinBtn = pinBtn;
    row._pinLabel = pinBtn.querySelector(".row-action-label");
    row._actions = [pinBtn, deleteBtn];

    pinBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeRow(row);
      toggleCurrentPin(id);
    });
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeRow(row);
      // deleteCurrentEvent asks for confirmation itself, which is exactly the
      // popup the swipe path is required to show.
      deleteCurrentEvent(id);
    });

    bindSwipe(row);
    return row;
  }

  function setRowOffset(row, offset, animate = false) {
    const card = row._card;
    // An empty string would fall back to the 150ms transform transition on
    // .event-card and make the card lag a dragging finger.
    card.style.transition = animate
      ? "transform 220ms cubic-bezier(0.22, 1, 0.36, 1)"
      : "none";
    card.style.transform = offset ? `translateX(${offset}px)` : "";

    const isOpen = Math.abs(offset) > 1;
    row.classList.toggle("is-open", isOpen);
    row._actions.forEach((button) => { button.tabIndex = isOpen ? 0 : -1; });
  }

  function closeRow(row, animate = true) {
    if (!row) return false;
    setRowOffset(row, 0, animate);
    if (openRow === row) openRow = null;
    return true;
  }

  function closeOpenRow() {
    if (!openRow) return false;
    closeRow(openRow);
    return true;
  }

  function bindSwipe(row) {
    const card = row._card;
    let startX = 0, startY = 0, delta = 0;
    let pointerId = null, decided = false, dragging = false;

    card.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pointerId = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      delta = 0;
      decided = false;
      dragging = false;
      row._swiped = false;
    });

    card.addEventListener("pointermove", (e) => {
      if (e.pointerId !== pointerId) return;
      const mx = e.clientX - startX;
      const my = e.clientY - startY;

      if (!decided) {
        if (Math.abs(mx) < SWIPE_SLOP && Math.abs(my) < SWIPE_SLOP) return;
        decided = true;
        // A gesture that is mostly vertical belongs to the scroller, and once
        // it has been handed over it is never taken back mid-drag.
        dragging = Math.abs(mx) > Math.abs(my) * 1.4;
        if (dragging && openRow && openRow !== row) closeRow(openRow);
      }
      if (!dragging) return;

      e.preventDefault();
      delta = Math.max(-SWIPE_WIDTH, Math.min(SWIPE_WIDTH, mx));
      setRowOffset(row, delta);
    }, { passive: false });

    const settle = (e) => {
      if (e.pointerId !== pointerId) return;
      pointerId = null;
      if (!dragging) return;

      dragging = false;
      row._swiped = true;                 // cleared on the next pointerdown

      if (Math.abs(delta) >= SWIPE_TRIGGER) {
        setRowOffset(row, delta > 0 ? SWIPE_WIDTH : -SWIPE_WIDTH, true);
        openRow = row;
        try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
      } else {
        closeRow(row);
      }
    };

    card.addEventListener("pointerup", settle);
    card.addEventListener("pointercancel", settle);
  }

  // Pinned events are sorted first by the server, so that block is always
  // contiguous and a single header can cover it. Checking pinned before the
  // day is what keeps the two schemes from fighting.
  function sectionFor(event, cd) {
    if (event.pinned) return "pinned";
    if (cd.totalDays === 0) return "today";
    if (cd.totalDays === 1) return "tomorrow";
    return "later";
  }

  function sectionHeader(key) {
    let node = headerIndex.get(key);
    if (node) return node;

    node = document.createElement("div");
    node.className = `event-section section-${key}`;
    node.setAttribute("role", "presentation");
    node.innerHTML = '<span class="event-section-label"></span>';
    node.firstChild.textContent = t(SECTION_LABELS[key] || key);
    headerIndex.set(key, node);
    return node;
  }

  // Mirrors the logic openEditComposer uses for the reminder field, so the
  // time on the card and the time in the form can never disagree.
  function reminderTimeText(event) {
    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (spec && spec.mode === "relative") return null;
    const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9));
    const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0));
    return `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
  }

  function buildCard(id) {
    const art = document.createElement("article");
    art.className = "event-card";
    art.tabIndex = 0;
    art.setAttribute("role", "button");
    art.dataset.id = id;
    art.innerHTML = `
      <div class="event-card-top">
        <div class="event-head">
          <h3 class="event-title" data-f="title"></h3>
          <div class="event-badges">
            <span class="badge badge-pin" data-f="pin" hidden>${CARD_ICONS.pin}<span data-f="pinText"></span></span>
            <span class="badge" data-f="cat"></span>
            <span class="urgency-badge" data-f="urgency"></span>
          </div>
        </div>
        <span class="event-repeat" data-f="repeat"></span>
      </div>

      <div class="event-progress-wrap">
        <div class="event-progress-bar">
          <div class="event-progress-fill" data-f="fill"></div>
        </div>
        <span class="event-progress-label" data-f="progress"></span>
      </div>

      <div class="event-dates">
        <span class="event-date-item">${CARD_ICONS.calendar}<span data-f="iso"></span></span>
        <span class="event-dates-sep">•</span>
        <span class="event-date-item">${CARD_ICONS.moon}<span data-f="jalali"></span></span>
        <span class="event-date-item event-reminder" data-f="reminderWrap" hidden>${CARD_ICONS.bell}<span data-f="reminder"></span></span>
      </div>

      <div class="event-bottom">
        <span class="status-dot" data-f="dot"></span>
        <span data-f="status"></span>
      </div>
    `;

    const fields = {};
    art.querySelectorAll("[data-f]").forEach((el) => { fields[el.dataset.f] = el; });
    art._f = fields;

    // Reading the id off the element at call time rather than closing over it
    // means a reused node can never open the wrong event.
    const open = () => {
      // The click that ends a swipe should put the row back rather than open
      // the sheet — otherwise every drag lands you in the detail view.
      const row = art.parentElement;
      if (row && row._swiped) return;
      if (closeOpenRow()) return;
      openDetail(art.dataset.id);
    };
    art.addEventListener("click", open);
    art.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });

    return wrapInRow(art, id);
  }

  function fillCard(row, event, cd) {
    const art = row._card || row;
    const f = art._f;

    if (row._pinLabel) {
      const pinLabel = t(event.pinned ? "Unpin" : "Pin");
      row._pinLabel.textContent = pinLabel;
      row._pinBtn.setAttribute("aria-label", `${pinLabel}: ${event.title || ""}`);
      row._actions[1].setAttribute("aria-label", `${t("Delete")}: ${event.title || ""}`);
      row.classList.toggle("row-pinned", !!event.pinned);
    }
    const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";

    art.className = `event-card cat-${event.category || "general"} tone-${cd.tone}`;
    art.setAttribute("aria-label", t("Open details for {title}", { title: event.title }));

    f.title.textContent = event.title || "";
    f.pin.hidden = !event.pinned;
    f.pinText.textContent = t("Pinned");
    f.cat.className = `badge ${getCatBadgeClass(event.category)}`;
    f.cat.textContent = catLabel;
    f.urgency.className = `urgency-badge urgency-${cd.tone}`;
    f.urgency.textContent = cd.shortText;
    f.repeat.textContent = t(REPEAT_LABELS[event.repeat] || "One time");

    const pct = cd.totalDays <= 0
      ? 100
      : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));
    f.fill.style.width = `${pct}%`;
    f.progress.textContent = cd.totalDays <= 0 ? t("Today!") : `${cd.totalDays}d`;

    const iso = event.next_date_iso || event.date_iso || "—";
    f.iso.textContent = event.all_day === false && event.time_hm
      ? `${iso} · ${event.time_hm}`
      : iso;
    f.jalali.textContent = event.next_date_jalali || event.date_jalali || "—";

    const reminder = reminderTimeText(event);
    f.reminderWrap.hidden = !reminder;
    if (reminder) f.reminder.textContent = reminder;

    const status = event.notify_status || "pending";
    f.dot.className = `status-dot status-${status}`;
    f.status.textContent = t(STATUS_LABELS[status] || "Pending");
  }

  function renderEvents() {
    if (!els.eventsWrap) return;

    const wrap = els.eventsWrap;
    if (!state.filteredEvents.length) {
      clearCards();
      showStatePanel();
      return;
    }

    // A render means the data moved underneath any open row, so the revealed
    // buttons would no longer belong to the card sitting above them.
    closeOpenRow();

    const animate = !prefersReducedMotion();
    const before = animate ? snapshotTops(wrap) : null;

    const desired = [];
    const live = new Set();
    const fresh = [];
    let section = null;

    state.filteredEvents.forEach((event) => {
      const cd = getCountdownData(event.next_date_iso || event.date_iso);
      const key = sectionFor(event, cd);
      if (key !== section) {
        section = key;
        desired.push(sectionHeader(key));
      }

      let art = cardIndex.get(event.id);
      if (!art) {
        art = buildCard(event.id);
        cardIndex.set(event.id, art);
        fresh.push(art);
      }
      fillCard(art, event, cd);
      live.add(event.id);
      desired.push(art);
    });

    cardIndex.forEach((art, id) => {
      if (!live.has(id)) {
        art.remove();
        cardIndex.delete(id);
      }
    });

    const wanted = new Set(desired);
    Array.from(wrap.children).forEach((node) => {
      if (!wanted.has(node)) node.remove();
    });
    desired.forEach((node, i) => {
      if (wrap.children[i] !== node) wrap.insertBefore(node, wrap.children[i] || null);
    });

    if (animate) {
      flipFrom(before, wrap);
      playEntries(fresh);
    }
    showStatePanel();
  }

  /* ── Movement ────────────────────────────────────────
     FLIP: measure where everything was, let the reordering happen, measure
     again, then animate the difference away. Only the vertical offset matters
     in a single column, so one number per node is enough. */

  function snapshotTops(wrap) {
    const tops = new Map();
    Array.from(wrap.children).forEach((node) => {
      tops.set(node, node.getBoundingClientRect().top);
    });
    return tops;
  }

  function flipFrom(before, wrap) {
    if (!before) return;
    Array.from(wrap.children).forEach((node) => {
      const was = before.get(node);
      if (was === undefined) return;                 // new — gets the entry animation
      const delta = was - node.getBoundingClientRect().top;
      if (Math.abs(delta) < 1) return;
      node.animate(
        [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
        { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );
    });
  }

  function playEntries(nodes) {
    nodes.forEach((node, i) => {
      node.animate(
        [
          { opacity: 0, transform: "translateY(10px) scale(0.98)" },
          { opacity: 1, transform: "none" },
        ],
        {
          duration: 260,
          delay: Math.min(i * 40, 240),
          easing: "cubic-bezier(0.22, 1, 0.36, 1)",
          fill: "backwards",
        }
      );
    });
  }

  function getCatBadgeClass(cat) {
    const map = {
      birthday: "badge-cat-birthday", work:    "badge-cat-work",
      family:   "badge-cat-family",   health:  "badge-cat-health",
      travel:   "badge-cat-travel",   finance: "badge-cat-finance",
      study:    "badge-cat-study",
    };
    return map[cat] || "";
  }

  /* ── Sheet Management ────────────────────────────────── */
  function openSheet(name, focusTgt = null) {
    state.lastFocusedElement = document.activeElement;
    if (els.sheetOverlay) els.sheetOverlay.hidden = false;

    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      const active = sheet.id === name;
      sheet.hidden = !active;
      sheet.setAttribute("aria-hidden", String(!active));
    });

    state.activeSheet = name;
    if (els.openComposerBtn) {
      els.openComposerBtn.setAttribute("aria-expanded", String(name === "composerSheet"));
    }
    updateTgBackButton();
    setTimeout(() => focusTgt?.focus?.(), 40);
  }

  function closeSheets() {
    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      sheet.hidden = true;
      sheet.setAttribute("aria-hidden", "true");
    });
    if (els.sheetOverlay) els.sheetOverlay.hidden = true;
    state.activeSheet = null;
    if (els.openComposerBtn) els.openComposerBtn.setAttribute("aria-expanded", "false");
    updateTgBackButton();
    state.lastFocusedElement?.focus?.();
  }

  function updateTgBackButton() {
    if (!tg?.BackButton) return;
    try {
      tg.BackButton.hide();
      tg.BackButton.offClick(handleTgBack);
      if (state.activeSheet) {
        tg.BackButton.onClick(handleTgBack);
        tg.BackButton.show();
      }
    } catch (_) {}
  }

  function handleTgBack() {
    if (closeDatePicker()) return;
    if (state.activeSheet) closeSheets();
  }

  /* ── Composer ────────────────────────────────────────── */
  // Kept in sync with the <option> values in index.html. Anything outside this
  // list (an event saved by a future build, say) falls back to one hour.
  const REMINDER_OFFSETS = [0, 15, 30, 60, 120, 1440, 10080];
  const DEFAULT_OFFSET_MINUTES = 60;

  // An all-day event has no start time, so "15 minutes before" means nothing:
  // it takes a wall-clock reminder instead. A timed event takes an offset.
  function updateAllDayVisibility() {
    const allDay = els.allDay ? els.allDay.checked : true;
    if (els.eventTimeWrap)      els.eventTimeWrap.hidden      = allDay;
    if (els.reminderTimeWrap)   els.reminderTimeWrap.hidden   = !allDay;
    if (els.reminderOffsetWrap) els.reminderOffsetWrap.hidden = allDay;
  }

  function updateRepeatUntilVisibility() {
    if (!els.repeatUntilWrap) return;
    const isRecurring = !!(els.repeat?.value && els.repeat.value !== "none");
    els.repeatUntilWrap.hidden = !isRecurring;
    if (!isRecurring && els.repeatUntil) els.repeatUntil.value = "";
    // Can't pick an end date before the event's own start date.
    if (els.repeatUntil && els.date?.value) els.repeatUntil.min = els.date.value;
  }

  function resetComposer() {
    els.eventForm?.reset();
    if (els.eventId)        els.eventId.value       = "";
    if (els.dateJalali)     els.dateJalali.value     = "";
    if (els.noteCharCount)  els.noteCharCount.textContent = "0 / 2000";
    state.editingEventId = null;
    if (els.composerTitle)    els.composerTitle.textContent    = t("New Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Set title, date and repeat pattern.";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.saveEventBtn)     els.saveEventBtn.innerHTML       = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      Save Event`;
  }

  function openCreateComposer() {
    resetComposer();
    openSheet("composerSheet", els.title);
  }

  function openEditComposer(event) {
    state.editingEventId = event.id;
    if (els.eventId)    els.eventId.value    = event.id;
    if (els.title)      els.title.value      = event.title      || "";
    if (els.date)       els.date.value       = event.date_iso   || "";
    if (els.dateJalali) els.dateJalali.value = event.date_jalali|| "";
    if (els.repeat)     els.repeat.value     = event.repeat     || "none";
    if (els.category)   els.category.value   = event.category   || "general";
    const allDay = event.all_day !== false;
    if (els.allDay)    els.allDay.checked = allDay;
    if (els.eventTime) els.eventTime.value = event.time_hm || "09:00";

    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (els.reminderTime) {
      const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9)).padStart(2, "0");
      const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0)).padStart(2, "0");
      els.reminderTime.value = `${h}:${m}`;
    }
    if (els.reminderOffset) {
      const offset = spec?.mode === "relative" ? Number(spec.offset_minutes) : DEFAULT_OFFSET_MINUTES;
      els.reminderOffset.value = String(
        REMINDER_OFFSETS.includes(offset) ? offset : DEFAULT_OFFSET_MINUTES
      );
    }
    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.pin)        els.pin.checked      = !!event.pinned;
    if (els.note)       els.note.value       = event.note       || "";
    if (els.noteCharCount) {
      els.noteCharCount.textContent = `${(event.note || "").length} / 2000`;
    }
    if (els.composerTitle)    els.composerTitle.textContent    = t("Edit Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Update the event details.";
    if (els.saveEventBtn)     els.saveEventBtn.textContent     = "Save Changes";
    openSheet("composerSheet", els.title);
  }

  /* ── Detail Panel ────────────────────────────────────── */
  function getEventById(id) {
    return state.events.find((e) => e.id === id) ?? null;
  }

  function openDetail(eventId) {
    const ev = getEventById(eventId);
    if (!ev) return;

    state.detailEventId = eventId;
    const cd = getCountdownData(ev.next_date_iso || ev.date_iso);

    // Basic fields
    if (els.detailEventTitle)    els.detailEventTitle.textContent    = ev.title || "—";
    if (els.detailCategoryBadge) {
      els.detailCategoryBadge.textContent = CATEGORY_LABELS[ev.category] || "General";
      els.detailCategoryBadge.className = `badge ${getCatBadgeClass(ev.category)}`;
    }
    if (els.detailRepeatBadge)  els.detailRepeatBadge.textContent  = REPEAT_LABELS[ev.repeat]  || "One time";
    if (els.detailPinnedBadge)  els.detailPinnedBadge.hidden        = !ev.pinned;
    if (els.detailDateIso) {
      els.detailDateIso.textContent =
        (ev.date_iso || "—") +
        (ev.all_day === false && ev.time_hm ? `  ·  ${ev.time_hm}` : "");
    }
    if (els.detailDateJalali)   els.detailDateJalali.textContent    = ev.date_jalali || "—";
    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
    if (els.detailStatus)       els.detailStatus.textContent        = t(STATUS_LABELS[ev.notify_status] || "—");
    if (els.detailNote)         els.detailNote.value                = ev.note        || "";

    // Pin button label
    if (els.detailPinBtn) {
      els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
    }

    // Countdown ring
    if (els.countdownDays) {
      els.countdownDays.textContent = cd.totalDays <= 0 ? "🎉" : String(Math.abs(cd.totalDays));
    }
    if (els.countdownRing) {
      els.countdownRing.className = `countdown-ring${
        cd.tone === "past"  ? " is-past"  :
        cd.tone === "today" ? " is-today" : ""
      }`;
    }
    if (els.detailCountdownText) {
      els.detailCountdownText.textContent = cd.fullText;
    }

    openSheet("detailSheet", els.detailNote);
  }

  /* ── Form Submit (Add / Edit) ────────────────────────── */
  async function submitEventForm(e) {
    e.preventDefault();

    const allDay = els.allDay ? els.allDay.checked : true;
    const eventTime = (els.eventTime?.value || "").trim();
    const [timeH, timeM] = (els.reminderTime?.value || "09:00").split(":");

    const reminders = allDay
      ? [{ mode: "absolute", hour: Number(timeH ?? 9), minute: Number(timeM ?? 0) }]
      : [{ mode: "relative", offset_minutes: Number(els.reminderOffset?.value ?? DEFAULT_OFFSET_MINUTES) }];

    const payload = {
      title:    els.title?.value.trim()    || "",
      date:     els.date?.value            || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      // Travels with the event so the worker, which has no initData when it
      // fires a reminder, knows which language to write it in.
      lang: currentLang,
      repeat:   els.repeat?.value          || "none",
      category: els.category?.value        || "general",
      note:     els.note?.value.trim()     || "",
      pinned:   !!els.pin?.checked,
      all_day:  allDay,
      time_hm:  allDay ? null : eventTime,
      reminders,
      // Still sent so an older server build keeps scheduling correctly.
      reminder_hour: Number(timeH ?? 9),
      reminder_minute: Number(timeM ?? 0),
      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
    };

    if (!payload.title) {
      showToast(t("Please enter an event title."), "error");
      els.title?.focus();
      return;
    }
    if (!payload.date) {
      showToast(t("Please select a date."), "error");
      els.date?.focus();
      return;
    }
    if (!allDay && !eventTime) {
      showToast(t("Please set the event time, or mark it as an all-day event."), "error");
      els.eventTime?.focus();
      return;
    }

    setLoading(true);
    try {
      if (state.editingEventId) {
        // ✅ FIX: event_id (was: eventid)
        await apiPost("/api/edit", { event_id: state.editingEventId, ...payload });
        showToast(t("Event updated successfully."), "success");
      } else {
        await apiPost("/api/add", payload);
        showToast(t("Event saved! You'll receive a reminder in Telegram."), "success");
      }
      closeSheets();
      resetComposer();
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Delete ──────────────────────────────────────────── */
  // Takes an id now so a swipe can reach an event that is not open in the
  // sheet. The default keeps every existing call site working untouched.
  async function deleteCurrentEvent(eventId = state.detailEventId) {
    const ev = getEventById(eventId);
    if (!ev) return;

    // ✅ FIX: custom confirm dialog (window.confirm broken in Telegram WebView)
    const ok = await showConfirm({
      title:   "Delete Event?",
      text:    `"${ev.title}" will be permanently removed.`,
      okLabel: "Delete",
      icon:    "🗑️",
    });
    if (!ok) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      await apiPost("/api/delete", { event_id: ev.id });
      closeSheets();
      showToast(t("Event deleted."), "success");
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Save Note ───────────────────────────────────────── */
  async function saveCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/note", {
        event_id: ev.id,
        note: els.detailNote?.value.trim() || "",
      });
      const target = getEventById(ev.id);
      if (target) target.note = data.note || "";
      showToast(t("Note saved."), "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  function resetCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev || !els.detailNote) return;
    els.detailNote.value = ev.note || "";
  }

  /* ── Pin ─────────────────────────────────────────────── */
  async function toggleCurrentPin(eventId = state.detailEventId) {
    try { tg?.HapticFeedback?.impactOccurred?.("light"); } catch (_) {}
    const ev = getEventById(eventId);
    if (!ev) return;

    const nextPinned = !ev.pinned;
    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/pin", { event_id: ev.id, pinned: nextPinned });
      ev.pinned = !!data.pinned;
      if (els.detailPinBtn)   els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
      if (els.detailPinnedBadge) els.detailPinnedBadge.hidden = !ev.pinned;
      await loadEvents();
      showToast(ev.pinned ? "Event pinned to top." : "Event unpinned.", "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Share ───────────────────────────────────────────── */
  async function shareCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    // Batch 12b: the picture card and the public link live in share.js.
    // The plain-text share below stays as the fallback for the case
    // where that file failed to load.
    if (window.TMShare && typeof window.TMShare.open === "function") {
      window.TMShare.open(ev);
      return;
    }

    const text = [
      `📅 ${ev.title}`,
      `📆 Gregorian: ${ev.date_iso}`,
      ev.all_day === false && ev.time_hm ? `🕒 Time: ${ev.time_hm}` : "",
      `🗓️ Jalali: ${ev.date_jalali}`,
      `🔄 Repeat: ${REPEAT_LABELS[ev.repeat] || "One time"}`,
      `🏷️ Category: ${t(CATEGORY_PLAIN[ev.category] || "General")}`,
      ev.note ? `📝 Note: ${ev.note}` : "",
    ].filter(Boolean).join("\n");

    try {
      if (navigator.share) {
        await navigator.share({ title: ev.title, text });
        showToast(t("Shared!"), "success");
        return;
      }
      await copyToClipboard(text);
      showToast(t("Event details copied to clipboard."), "success");
    } catch (_) {
      showToast(t("Could not share. Please try copying manually."), "error");
    }
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:absolute;left:-9999px;top:0";
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }

  /* ── Jalali / Gregorian Sync ─────────────────────────── */
  function format2(n) { return String(n).padStart(2, "0"); }

  // Julian-day based conversion (the jalaali-js algorithm). The previous pair
  // used a 33-year approximation and, worse, assigned to `gy` inside its own
  // `let` initialiser — a temporal dead zone violation that threw for every
  // year above 979, i.e. every real date. Jalali input silently never reached
  // the Gregorian field. This version was checked against the jdatetime output
  // the server produces, for all 25,567 days from 1990 to 2060: no differences
  // and no round-trip failures.
  function div(a, b) { return ~~(a / b); }
  function mod(a, b) { return a - ~~(a / b) * b; }

  const JALALI_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
                         1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];

  function jalCal(jy) {
    const bl = JALALI_BREAKS.length;
    const gy = jy + 621;
    let leapJ = -14;
    let jp = JALALI_BREAKS[0];
    if (jy < jp || jy >= JALALI_BREAKS[bl - 1]) throw new RangeError("year out of range");

    let jump = 0;
    for (let i = 1; i < bl; i += 1) {
      const jm = JALALI_BREAKS[i];
      jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }

    let n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;

    const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    const march = 20 + leapJ - leapG;

    if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
    let leap = mod(mod(n + 1, 33) - 1, 4);
    if (leap === -1) leap = 4;

    return { leap, gy, march };
  }

  function gregorianToJulian(gy, gm, gd) {
    let d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4)
          + div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
    return d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
  }

  function julianToGregorian(jdn) {
    let j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    const i = div(mod(j, 1461), 4) * 5 + 308;
    const gd = div(mod(i, 153), 5) + 1;
    const gm = mod(div(i, 153), 12) + 1;
    const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy, gm, gd };
  }

  function jalaliToGregorian(jy, jm, jd) {
    const r = jalCal(jy);
    return julianToGregorian(
      gregorianToJulian(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1
    );
  }

  function gregorianToJalali(gy, gm, gd) {
    let jy = gy - 621;
    const r = jalCal(jy);
    let k = gregorianToJulian(gy, gm, gd) - gregorianToJulian(r.gy, 3, r.march);

    if (k >= 0) {
      if (k <= 185) return { jy, jm: 1 + div(k, 31), jd: mod(k, 31) + 1 };
      k -= 186;
    } else {
      // Previous Jalali year. The leap flag is the one computed for the year we
      // started from, not for the decremented year.
      jy -= 1;
      k += 179;
      if (r.leap === 1) k += 1;
    }

    return { jy, jm: 7 + div(k, 30), jd: mod(k, 30) + 1 };
  }

  function daysInJalaliMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    // jalCal returns the number of years since the last leap year, so zero —
    // not one — is what marks a leap year. Esfand has 30 days only then.
    return jalCal(jy).leap === 0 ? 30 : 29;
  }

  function syncJalaliFromGregorian() {
    const val = els.date?.value;
    if (!val) { if (els.dateJalali) els.dateJalali.value = ""; return; }
    const [gy, gm, gd] = val.split("-").map(Number);
    if (!gy || !gm || !gd) return;
    const j = gregorianToJalali(gy, gm, gd);
    if (els.dateJalali) els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function syncGregorianFromJalali() {
    const raw = (els.dateJalali?.value || "").trim().replace(/-/g, "/");
    if (!raw) return;
    const parts = raw.split("/");
    if (parts.length !== 3) return;
    const [jy, jm, jd] = parts.map(Number);
    if (!jy || !jm || !jd) return;
    try {
      const g = jalaliToGregorian(jy, jm, jd);
      if (els.date) els.date.value = `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
    } catch (_) {
      // Out-of-range year: leave the Gregorian field as it was rather than
      // letting the error escape the change handler.
    }
  }

  /* ── Escape HTML ─────────────────────────────────────── */
  function escapeHtml(v) {
    return String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /* ── Event Bindings ──────────────────────────────────── */
  function bindEvents() {
    // Header / nav
    els.refreshBtn?.addEventListener("click", () => loadEvents());
    els.retryBtn?.addEventListener("click",   () => loadEvents());
    els.emptyAddBtn?.addEventListener("click", openCreateComposer);
    els.openComposerBtn?.addEventListener("click", openCreateComposer);
    els.closeComposerX?.addEventListener("click", closeSheets);
    els.closeDetailX?.addEventListener("click",   closeSheets);
    els.cancelBtn?.addEventListener("click",      closeSheets);
    els.sheetOverlay?.addEventListener("click",   closeSheets);

    // Form
    els.eventForm?.addEventListener("submit", submitEventForm);
    els.date?.addEventListener("change", syncJalaliFromGregorian);
    els.date?.addEventListener("change", updateRepeatUntilVisibility);
    els.repeat?.addEventListener("change", updateRepeatUntilVisibility);
    els.allDay?.addEventListener("change", updateAllDayVisibility);
    els.dateJalali?.addEventListener("change", syncGregorianFromJalali);
    els.dateJalali?.addEventListener("blur",   syncGregorianFromJalali);

    // Note char counter
    els.note?.addEventListener("input", () => {
      const len = els.note.value.length;
      if (els.noteCharCount) els.noteCharCount.textContent = `${len} / 2000`;
    });

    // Search — debounced, because every keystroke would otherwise be a round
    // trip and would burn through the per-minute budget in a few seconds.
    let searchTimer = null;
    els.searchInput?.addEventListener("input", (e) => {
      const value = e.target.value || "";
      if (value.trim() === state.searchTerm.trim()) return;

      state.searchTerm = value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadEvents(), SEARCH_DEBOUNCE_MS);
    });

    // Filters
    els.filterButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = btn.dataset.filter || "all";
        els.filterButtons.forEach((b) => b.classList.toggle("is-active", b === btn));
        if (next === state.currentFilter) return;

        state.currentFilter = next;
        clearTimeout(searchTimer);
        loadEvents();
      });
    });

    // Detail actions
    els.detailEditBtn?.addEventListener("click",        () => openEditComposer(getEventById(state.detailEventId)));
    els.detailDeleteBtn?.addEventListener("click",      deleteCurrentEvent);
    els.detailPinBtn?.addEventListener("click",         toggleCurrentPin);
    els.detailShareBtn?.addEventListener("click",       shareCurrentEvent);
    els.detailNoteSaveBtn?.addEventListener("click",    saveCurrentNote);
    els.detailNoteCancelBtn?.addEventListener("click",  resetCurrentNote);

    // Load more
    els.loadMoreBtn?.addEventListener("click", () => loadEvents(true));

    // Onboarding
    els.onboardingSkipBtn?.addEventListener("click", completeOnboarding);
    els.onboardingNextBtn?.addEventListener("click", advanceOnboarding);

    // Keyboard
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (closeDatePicker()) return;
        if (els.confirmOverlay && !els.confirmOverlay.hidden) {
          els.confirmOverlay.hidden = true;
          return;
        }
        if (state.activeSheet) closeSheets();
      }
    });
  }

  /* ── Onboarding (first run only) ────────────────────── */
  const ONBOARDING_KEY = "tmp_onboarding_seen_v1";
  const ONBOARDING_STEPS = [
    {
      icon: "🗓️",
      title: "Never miss what matters",
      text: "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.",
    },
    {
      icon: "🔔",
      title: "Reminders come straight to Telegram",
      text: "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.",
    },
    {
      icon: "🌗",
      title: "Gregorian & Jalali, together",
      text: "Every date shows in both calendars automatically. Tap the + button below to add your first event.",
    },
  ];
  let onboardingStep = 0;

  function showOnboardingIfNeeded() {
    if (!els.onboardingOverlay) return;
    try {
      if (localStorage.getItem(ONBOARDING_KEY)) return;
    } catch (_) {
      return; // storage blocked (e.g. private mode) — don't force this on every load
    }
    onboardingStep = 0;
    renderOnboardingStep();
    els.onboardingOverlay.hidden = false;
    els.onboardingOverlay.setAttribute("aria-hidden", "false");
  }

  function renderOnboardingStep() {
    const step = ONBOARDING_STEPS[onboardingStep];
    if (els.onboardingIcon)  els.onboardingIcon.textContent  = step.icon;
    if (els.onboardingTitle) els.onboardingTitle.textContent = t(step.title);
    if (els.onboardingText)  els.onboardingText.textContent  = t(step.text);
    if (els.onboardingNextBtn) {
      els.onboardingNextBtn.textContent =
        onboardingStep === ONBOARDING_STEPS.length - 1 ? t("Get Started") : t("Next");
    }
    if (els.onboardingDots) {
      [...els.onboardingDots.children].forEach((dot, i) => {
        dot.classList.toggle("is-active", i === onboardingStep);
      });
    }
  }

  function advanceOnboarding() {
    try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    if (onboardingStep < ONBOARDING_STEPS.length - 1) {
      onboardingStep += 1;
      renderOnboardingStep();
    } else {
      completeOnboarding();
    }
  }

  function completeOnboarding() {
    if (els.onboardingOverlay) {
      els.onboardingOverlay.hidden = true;
      els.onboardingOverlay.setAttribute("aria-hidden", "true");
    }
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) {}
  }


  /* ── Date Picker ─────────────────────────────────────── */
  // A typed field cannot be got right in two calendars at once, so both dates
  // are picked instead. Selection rides on CSS scroll snapping: whichever item
  // settles under the highlight band is the value. No drag maths, and momentum
  // scrolling comes free from the browser.
  const DP_ITEM_H = 40;
  const DP_SETTLE_MS = 90;

  const MONTH_NAMES = {
    gregorian: ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"],
    jalali: ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
             "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"],
  };

  // The server rejects anything outside 1900–2200, so the Jalali range is
  // derived from that rather than guessed — every pickable Jalali date is
  // guaranteed to convert back inside the accepted window.
  const DP_YEAR_MIN = { gregorian: 1900 };
  const DP_YEAR_MAX = { gregorian: 2200 };
  DP_YEAR_MIN.jalali = gregorianToJalali(1900, 1, 1).jy + 1;
  DP_YEAR_MAX.jalali = gregorianToJalali(2200, 12, 31).jy - 1;

  const dp = {
    calendar: "gregorian",
    y: 0, m: 1, d: 1,
    onPick: null,
    allowClear: false,
    settleTimer: null,
  };

  function daysInGregorianMonth(gy, gm) {
    return new Date(Date.UTC(gy, gm, 0)).getUTCDate();
  }

  function dpDaysInMonth() {
    return dp.calendar === "jalali"
      ? daysInJalaliMonth(dp.y, dp.m)
      : daysInGregorianMonth(dp.y, dp.m);
  }

  function dpToIso() {
    if (dp.calendar === "jalali") {
      const g = jalaliToGregorian(dp.y, dp.m, dp.d);
      return `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
    }
    return `${dp.y}-${format2(dp.m)}-${format2(dp.d)}`;
  }

  function dpSetFromIso(iso) {
    const [gy, gm, gd] = String(iso || "").split("-").map(Number);
    const valid = gy && gm && gd && gy >= 1900 && gy <= 2200;
    const base = valid ? { gy, gm, gd } : (() => {
      const now = new Date();
      return { gy: now.getFullYear(), gm: now.getMonth() + 1, gd: now.getDate() };
    })();

    if (dp.calendar === "jalali") {
      const j = gregorianToJalali(base.gy, base.gm, base.gd);
      dp.y = j.jy; dp.m = j.jm; dp.d = j.jd;
    } else {
      dp.y = base.gy; dp.m = base.gm; dp.d = base.gd;
    }
  }

  function dpRange(from, to) {
    const out = [];
    for (let i = from; i <= to; i += 1) out.push(i);
    return out;
  }

  function dpFillWheel(el, values, labels, selected) {
    if (!el) return;
    el.innerHTML = values
      .map((value, i) => `<div class="dp-item" role="option" data-value="${value}"` +
                         ` aria-selected="${value === selected}">${escapeHtml(labels[i])}</div>`)
      .join("");
    dpScrollTo(el, Math.max(0, values.indexOf(selected)));
  }

  function dpScrollTo(el, index) {
    const top = index * DP_ITEM_H;
    el.scrollTop = top;

    // scroll-snap-type is mandatory on these columns, and the snap re-runs on
    // the layout that follows a rebuild — often dragging the column back to
    // the nearest snap point. Re-asserting on the next frame makes the
    // intended item stick.
    requestAnimationFrame(() => {
      if (Math.abs(el.scrollTop - top) > 1) el.scrollTop = top;
    });
  }

  function dpRender(scope = "all") {
    const yMin = DP_YEAR_MIN[dp.calendar];
    const yMax = DP_YEAR_MAX[dp.calendar];
    dp.y = Math.min(Math.max(dp.y, yMin), yMax);
    dp.m = Math.min(Math.max(dp.m, 1), 12);
    dp.d = Math.min(Math.max(dp.d, 1), dpDaysInMonth());

    // Rebuilding a wheel resets its scrollTop, so the two the user is not
    // touching are left alone while a scroll is settling.
    if (scope === "all") {
      const years = dpRange(yMin, yMax);
      dpFillWheel(els.dpYear, years, years.map(String), dp.y);

      const months = dpRange(1, 12);
      dpFillWheel(els.dpMonth, months,
                  MONTH_NAMES[dp.calendar].map((name) => t(name)), dp.m);
    }
    // The day column is always rebuilt: its length depends on the month and,
    // in Esfand, on whether the year is a leap year.
    const days = dpRange(1, dpDaysInMonth());
    dpFillWheel(els.dpDay, days, days.map(String), dp.d);

    dpRenderPreview();
  }

  function dpRenderPreview() {
    if (!els.dpPreview) return;
    const iso = dpToIso();
    const [gy, gm, gd] = iso.split("-").map(Number);
    const j = gregorianToJalali(gy, gm, gd);
    els.dpPreview.textContent = `${iso}  •  ${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function dpMarkSelected(el, value) {
    el?.querySelectorAll(".dp-item").forEach((item) => {
      item.setAttribute("aria-selected", String(Number(item.dataset.value) === value));
    });
  }

  function dpOnScroll(el, field) {
    clearTimeout(dp.settleTimer);
    dp.settleTimer = setTimeout(() => {
      const index = Math.round(el.scrollTop / DP_ITEM_H);
      const item = el.querySelectorAll(".dp-item")[index];
      if (!item) return;

      const value = Number(item.dataset.value);
      if (value === dp[field]) return;

      dp[field] = value;
      dpMarkSelected(el, value);

      // Month length changes with the month and with the Jalali leap year, so
      // the day column is rebuilt whenever either of the others moves.
      if (field === "d") dpRenderPreview();
      else dpRender("days");
    }, DP_SETTLE_MS);
  }

  function openDatePicker({ value, onPick, allowClear = false, calendar = "gregorian" }) {
    dp.onPick = onPick;
    dp.allowClear = allowClear;
    dp.calendar = calendar;
    dpSetFromIso(value);

    els.dpTabs.forEach((tab) => {
      const active = tab.dataset.calendar === dp.calendar;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    if (els.dpClear) els.dpClear.hidden = !allowClear;

    state.lastFocusedElement = document.activeElement;

    // Order matters here, and getting it wrong is what made the picker open on
    // 1900 / January / 1. While the overlay is hidden the wheels have no
    // scroll box, so the scrollTop that centres today was silently dropped and
    // every column stayed parked on its first item. Show first, fill second.
    if (els.dpOverlay) {
      els.dpOverlay.hidden = false;
      els.dpOverlay.setAttribute("aria-hidden", "false");
    }
    dpRender();
    setTimeout(() => els.dpConfirm?.focus?.(), 40);
  }

  function closeDatePicker() {
    if (!els.dpOverlay || els.dpOverlay.hidden) return false;
    els.dpOverlay.hidden = true;
    els.dpOverlay.setAttribute("aria-hidden", "true");
    dp.onPick = null;
    state.lastFocusedElement?.focus?.();
    return true;
  }

  function setEventDate(iso) {
    if (els.date) els.date.value = iso;
    if (els.dateJalali) {
      if (!iso) { els.dateJalali.value = ""; return; }
      const [gy, gm, gd] = iso.split("-").map(Number);
      const j = gregorianToJalali(gy, gm, gd);
      els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
    }
  }

  function bindDatePicker() {
    const openForEventDate = (calendar) => () => openDatePicker({
      value: els.date?.value,
      calendar,
      onPick: setEventDate,
    });

    els.date?.addEventListener("click", openForEventDate("gregorian"));
    els.dateJalali?.addEventListener("click", openForEventDate("jalali"));
    els.repeatUntil?.addEventListener("click", () => openDatePicker({
      value: els.repeatUntil.value,
      allowClear: true,
      onPick: (iso) => { els.repeatUntil.value = iso; },
    }));

    [[els.dpYear, "y"], [els.dpMonth, "m"], [els.dpDay, "d"]].forEach(([el, field]) => {
      el?.addEventListener("scroll", () => dpOnScroll(el, field), { passive: true });
      el?.addEventListener("click", (e) => {
        const item = e.target.closest(".dp-item");
        if (item) el.scrollTo({ top: item.offsetTop - el.offsetTop, behavior: "smooth" });
      });
      el?.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        el.scrollBy({ top: step * DP_ITEM_H, behavior: "smooth" });
      });
    });

    els.dpTabs.forEach((tab) => tab.addEventListener("click", () => {
      const next = tab.dataset.calendar;
      if (next === dp.calendar) return;
      const iso = dpToIso();          // convert through the current selection
      dp.calendar = next;
      dpSetFromIso(iso);
      els.dpTabs.forEach((other) => {
        const active = other === tab;
        other.classList.toggle("is-active", active);
        other.setAttribute("aria-selected", String(active));
      });
      dpRender();
    }));

    els.dpToday?.addEventListener("click", () => {
      const now = new Date();
      dpSetFromIso(`${now.getFullYear()}-${format2(now.getMonth() + 1)}-${format2(now.getDate())}`);
      dpRender();
    });

    els.dpConfirm?.addEventListener("click", () => {
      const pick = dp.onPick;
      const iso = dpToIso();
      closeDatePicker();
      pick?.(iso);
      try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    });

    els.dpClear?.addEventListener("click", () => {
      const pick = dp.onPick;
      closeDatePicker();
      pick?.("");
    });

    els.dpCancel?.addEventListener("click", closeDatePicker);
    els.dpOverlay?.addEventListener("click", (e) => {
      if (e.target === els.dpOverlay) closeDatePicker();
    });
  }

  /* ── Published surface ───────────────────────────────
     views.js lives outside this closure and needs three things from it: a way
     into the detail sheet, the loaded events, and the Jalali conversion. The
     last one matters most — a second date conversion in another file is how
     two parts of the same app start disagreeing about what day it is. */
  window.TMApp = {
    openDetail,
    getEvent: getEventById,
    reload: loadEvents,
    language: () => currentLang,
    jalali: { fromGregorian: gregorianToJalali, toGregorian: jalaliToGregorian,
              daysInMonth: daysInJalaliMonth },
  };

  /* ── Boot ────────────────────────────────────────────── */
  // Before anything renders: the DOM pass rewrites the static markup, and
  // every later render reads currentLang through t().
  applyLanguage();
  initTelegram();
  bindEvents();
  bindDatePicker();
  loadEvents();
  showOnboardingIfNeeded();
})();
