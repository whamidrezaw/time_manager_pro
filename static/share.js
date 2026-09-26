/* ──────────────────────────────────────────────────────────────
   share.js — Batch 12b, the share sheet.

   Standalone like referral.js: it builds its own markup and reads what it
   needs from Telegram directly, so app.js keeps working untouched if this
   file ever fails to load. app.js calls window.TMShare.open(event) and
   falls back to its old text share when this object is absent. The sheet
   itself opens through static/modal.js (window.TMModal), which owns focus,
   Escape and the stack.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "Share event": "اشتراک‌گذاری رویداد",
    "Close": "بستن",
    "Public link": "لینک عمومی",
    "Anyone with the link can see this event's title and date.":
      "هر کسی که این لینک را داشته باشد، عنوان و تاریخ این رویداد را می‌بیند.",
    "Turn on public sharing?": "اشتراک‌گذاری عمومی روشن شود؟",
    "The title, date and countdown of this event become visible to anyone who opens the link. You can switch it off at any time, and the old link stops working.":
      "عنوان، تاریخ و شمارش معکوس این رویداد برای هر کسی که لینک را باز کند دیده می‌شود. هر وقت بخواهی می‌توانی خاموشش کنی و آن‌وقت لینک قبلی دیگر کار نمی‌کند.",
    "Turn on": "روشن کن",
    "Cancel": "انصراف",
    "Send in Telegram": "ارسال در تلگرام",
    "Copy link": "کپی لینک",
    "Copied": "کپی شد",
    "Turn on sharing to send the card.": "برای ارسال کارت، اشتراک‌گذاری را روشن کن.",
    "Could not load sharing.": "وضعیت اشتراک‌گذاری بارگذاری نشد.",
    "Something went wrong.": "مشکلی پیش آمد.",
    "Share the event itself with someone": "این رویداد را با کسی مشترک کن",
    "Public link is on. Switch it off any time.": "لینک عمومی روشن شد. هر وقت خواستی خاموشش کن.",
    "Let's keep this one in sync.": "بیا این را با هم هماهنگ نگه داریم.",
    "Anyone who opens this link gets their own copy, kept in step with yours.":
      "هر کسی این لینک را باز کند نسخهٔ خودش را می‌گیرد که با نسخهٔ تو همگام می‌ماند.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text) {
    return isFa && FA[text] ? FA[text] : text;
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (kind === "warning") tg.HapticFeedback.notificationOccurred("warning");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  var els = {};
  var current = null;
  var state = null;
  var pendingQuestion = null;
  var closing = null;

  async function api(path, body) {
    var response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ initData: (tg && tg.initData) || "" }, body)),
    });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  /* ── Markup ─────────────────────────────────────────── */

  function build() {
    if (els.overlay) return;

    // A native <dialog> shaped exactly like the overlay it replaces: full
    // screen, dimmed, the sheet at the bottom. Keeping that shape keeps the
    // look, the tap-outside-to-close and the public-link question as they
    // were — now in the top layer, where the detail page cannot cover them.
    var overlay = document.createElement("dialog");
    overlay.className = "shr-overlay";
    overlay.setAttribute("aria-labelledby", "shrTitle");
    overlay.innerHTML =
      '<div class="shr-dialog">' +
        '<div class="shr-handle" aria-hidden="true"></div>' +
        '<div class="shr-head">' +
          '<h2 class="shr-title" id="shrTitle" tabindex="-1" autofocus>' + t("Share event") + "</h2>" +
          '<button type="button" class="icon-btn shr-close" id="shrClose" aria-label="' +
            t("Close") + '">✕</button>' +
        "</div>" +
        '<div class="shr-preview" id="shrPreview"><div class="shr-skeleton"></div></div>' +
        '<div class="shr-toggle-row">' +
          '<div class="shr-toggle-copy">' +
            '<strong id="shrSwitchLabel">' + t("Public link") + "</strong>" +
            '<p id="shrSwitchHint">' + t("Anyone with the link can see this event's title and date.") + "</p>" +
          "</div>" +
          '<button type="button" class="shr-switch" id="shrSwitch" role="switch" ' +
            'aria-labelledby="shrSwitchLabel" aria-describedby="shrSwitchHint" ' +
            'aria-checked="false"><span class="shr-knob"></span></button>' +
        "</div>" +
        '<p class="shr-hint" id="shrHint"></p>' +
        '<div class="shr-actions">' +
          '<button type="button" class="btn-secondary" id="shrCopy">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="shrSend">' + t("Send in Telegram") + "</button>" +
        "</div>" +
        '<button type="button" class="btn-secondary shr-group" id="shrGroup">' +
          t("Share the event itself with someone") + "</button>" +
        '<p class="shr-hint" id="shrGroupHint"></p>' +
      "</div>";

    document.body.appendChild(overlay);
    if (isFa) overlay.setAttribute("dir", "rtl");

    els.overlay = overlay;
    els.preview = overlay.querySelector("#shrPreview");
    els.switch = overlay.querySelector("#shrSwitch");
    els.hint = overlay.querySelector("#shrHint");
    els.copy = overlay.querySelector("#shrCopy");
    els.send = overlay.querySelector("#shrSend");

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    overlay.querySelector("#shrClose").addEventListener("click", close);
    els.switch.addEventListener("click", onToggle);
    els.copy.addEventListener("click", copyLink);
    els.send.addEventListener("click", sendCard);

    els.group = overlay.querySelector("#shrGroup");
    els.groupHint = overlay.querySelector("#shrGroupHint");
    els.group.addEventListener("click", inviteToEvent);
  }

  function confirmPublic() {
    // The owner's own decision to make, so it is asked once, in plain words,
    // at the moment it takes effect — not buried in a settings screen. It is
    // a dialog of its own on the stack: it sits above the sheet, and Escape
    // or a back gesture answers it with "no" instead of reaching past it.
    return new Promise(function (resolve) {
      var box = document.createElement("dialog");
      box.className = "shr-confirm";
      box.setAttribute("aria-labelledby", "shrConfirmTitle");
      if (isFa) box.setAttribute("dir", "rtl");
      box.innerHTML =
        '<div class="shr-confirm-card">' +
          '<strong id="shrConfirmTitle">' + t("Turn on public sharing?") + "</strong>" +
          "<p>" + t("The title, date and countdown of this event become visible to anyone who opens the link. You can switch it off at any time, and the old link stops working.") + "</p>" +
          '<div class="shr-confirm-actions">' +
            '<button type="button" class="btn-secondary" data-answer="no" autofocus>' + t("Cancel") + "</button>" +
            '<button type="button" class="btn-primary" data-answer="yes">' + t("Turn on") + "</button>" +
          "</div>" +
        "</div>";

      box.addEventListener("click", function (event) {
        var answer = event.target.getAttribute("data-answer");
        if (!answer) return;
        window.TMModal.close(box, answer === "yes");
      });

      document.body.appendChild(box);
      pendingQuestion = box;
      window.TMModal.open(box, {
        onClose: function (result) {
          pendingQuestion = null;
          box.remove();
          resolve(result === true);
        }
      });
    });
  }

  /* ── Rendering ──────────────────────────────────────── */

  function render() {
    if (!state) return;

    var on = !!state.enabled;
    els.switch.setAttribute("aria-checked", on ? "true" : "false");
    els.switch.classList.toggle("is-on", on);

    els.copy.disabled = !on;
    els.send.disabled = !on;
    els.hint.textContent = on ? "" : t("Turn on sharing to send the card.");

    if (on && state.card_url) {
      // Cache-busted per open so the day count in the picture is never the
      // one from yesterday's visit.
      // Named, and loaded from this page's own origin: WEBAPP_BASE_URL may not
      // be it, and the CSP only lets a page show images from its own.
      var own = new URL(state.card_url, location.href);
      var image = document.createElement("img");
      image.alt = state.card_alt || "";
      image.src = own.pathname + (own.search ? own.search + "&" : "?") + "t=" + Date.now();
      els.preview.textContent = "";
      els.preview.appendChild(image);
    } else {
      els.preview.innerHTML = '<div class="shr-skeleton"></div>';
    }
  }

  /* ── Actions ────────────────────────────────────────── */

  async function onToggle() {
    if (!current || !state) return;

    var next = !state.enabled;
    if (next && !(await confirmPublic())) return;

    haptic(next ? "success" : "warning");
    els.switch.disabled = true;

    try {
      state = await api("/api/share/toggle", { event_id: current, enabled: next });
      render();
    } catch (_) {
      els.hint.textContent = t("Something went wrong.");
    } finally {
      els.switch.disabled = false;
    }
  }

  function flash(button, text) {
    var original = button.textContent;
    button.textContent = text;
    setTimeout(function () {
      button.textContent = original;
    }, 1500);
  }

  function copyLink() {
    if (!state || !state.public_url) return;
    haptic("success");

    var done = function () {
      flash(els.copy, t("Copied"));
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(state.public_url).then(done, done);
    } else {
      done();
    }
  }

  function shareViaLink() {
    // Works on every client: the public URL carries Open Graph tags pointing
    // at the same card, so the chat preview still shows the picture.
    var url =
      "https://t.me/share/url?url=" + encodeURIComponent(state.public_url);
    if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  async function sendCard() {
    if (!state || !state.enabled) return;
    haptic("success");

    if (!tg || typeof tg.shareMessage !== "function") {
      shareViaLink();
      return;
    }

    els.send.disabled = true;
    try {
      var prepared = await api("/api/share/prepare", { event_id: current });
      tg.shareMessage(prepared.prepared_message_id);
    } catch (_) {
      // Bot API too old, or Telegram could not fetch the photo. Either way the
      // person still gets to share something.
      shareViaLink();
    } finally {
      els.send.disabled = false;
    }
  }

  // Animated: the sheet slides out before it leaves the top layer.
  function close() {
    if (!els.overlay || !window.TMModal.isOpen(els.overlay) || closing) return;
    els.overlay.classList.remove("is-open");
    closing = setTimeout(closeNow, 200);
  }

  function closeNow() {
    clearTimeout(closing);
    closing = null;
    if (els.overlay) window.TMModal.close(els.overlay);
  }

  // However it was closed: its own button, Escape, or Telegram's back button.
  function closed() {
    els.overlay.classList.remove("is-open");
    if (pendingQuestion) window.TMModal.close(pendingQuestion);
  }

  async function open(event) {
    if (!event || !event.id) return;
    haptic();
    build();

    current = event.id;
    state = null;
    els.preview.innerHTML = '<div class="shr-skeleton"></div>';
    els.hint.textContent = "";
    window.TMModal.open(els.overlay, { requestClose: close, onClose: closed });
    requestAnimationFrame(function () {
      els.overlay.classList.add("is-open");
    });

    try {
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

  // Sharing the picture and sharing the event are different things: one sends
  // a snapshot, the other links two calendars together. Same sheet, separate
  // buttons, so nobody links an account when they meant to post an image.
  async function inviteToEvent() {
    if (!current) return;
    haptic("success");
    els.group.disabled = true;

    try {
      var group = await api("/api/group/link", { event_id: current });
      // The public page, not the t.me deep link: Telegram renders that one as
      // the bot's profile card, and this one as the event itself. The page
      // behind it carries the button that adds the event to their calendar.
      var target = group.public_url || group.invite_url;
      var url = "https://t.me/share/url?url=" + encodeURIComponent(target)
        + "&text=" + encodeURIComponent(t("Let's keep this one in sync."));
      if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
      else window.open(url, "_blank");
      els.groupHint.textContent = t("Anyone who opens this link gets their own copy, kept in step with yours.");
    } catch (_) {
      els.groupHint.textContent = t("Something went wrong.");
    } finally {
      els.group.disabled = false;
    }
  }

  window.TMShare = { open: open };
})();
