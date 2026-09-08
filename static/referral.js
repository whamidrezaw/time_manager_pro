/* ──────────────────────────────────────────────────────────────
   referral.js — Batch 12a, the invite screen.

   Deliberately standalone rather than folded into app.js: everything it
   needs from Telegram it can read itself, and every element it shows it
   builds itself. Nothing in app.js has to change for this to work, and
   nothing here can break the event list if it throws.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var NEAR_LIMIT_RATIO = 0.8;

  var FA = {
    "Invite friends": "دعوت از دوستان",
    "Invite a friend": "دعوت از دوستان",
    "Your invite link": "لینک دعوت شما",
    "Share link": "ارسال لینک",
    "Copy link": "کپی لینک",
    "Copied": "کپی شد",
    "Close": "بستن",
    "events used": "رویداد استفاده شده",
    "Every {step} friends who join and save their first event raise your limit by {bonus} events.":
      "به ازای هر {step} دوستی که وارد شود و اولین رویدادش را ذخیره کند، {bonus} رویداد به سقف شما اضافه می‌شود.",
    "{n} more to go for +{bonus} events": "{n} دعوت دیگر تا +{bonus} رویداد",
    "You have reached the highest limit. Thank you!": "به بالاترین سقف رسیده‌اید. ممنون از شما!",
    "{valid} joined": "{valid} نفر پیوسته‌اند",
    "{pending} on the way": "{pending} نفر در راه",
    "Running out of space": "جا دارد تمام می‌شود",
    "You've used {used} of your {limit} events. Invite friends to get more.":
      "{used} از {limit} رویداد شما استفاده شده است. با دعوت دوستان سقف را بالا ببرید.",
    "Invite now": "همین حالا دعوت کن",
    "Join me on TimeManager Pro — never miss a birthday, meeting or deadline again.":
      "به تایم‌منیجر پرو بیا — دیگر هیچ تولد، جلسه یا مهلتی را از دست نده.",
    "Could not load your invite link.": "لینک دعوت بارگذاری نشد.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text, vars) {
    var out = isFa && FA[text] ? FA[text] : text;
    if (vars) {
      Object.keys(vars).forEach(function (key) {
        out = out.split("{" + key + "}").join(vars[key]);
      });
    }
    return out;
  }

  // Persian digits, so the numbers match the rest of the interface.
  function num(value) {
    var text = String(value);
    if (!isFa) return text;
    return text.replace(/[0-9]/g, function (d) {
      return "۰۱۲۳۴۵۶۷۸۹"[Number(d)];
    });
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  var state = null;
  var els = {};

  async function fetchState() {
    var response = await fetch("/api/referral", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: (tg && tg.initData) || "" }),
    });
    if (!response.ok) throw new Error("REFERRAL_FAILED");
    return response.json();
  }

  /* ── Markup ─────────────────────────────────────────── */

  function icon() {
    return (
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
      ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/>' +
      '<line x1="12" y1="22" x2="12" y2="7"/>' +
      '<path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/>' +
      '<path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>'
    );
  }

  function mountHeaderButton() {
    var header = document.querySelector(".app-header");
    if (!header || document.getElementById("refOpenBtn")) return;

    var button = document.createElement("button");
    button.type = "button";
    button.id = "refOpenBtn";
    button.className = "icon-btn ref-open-btn";
    button.setAttribute("aria-label", t("Invite friends"));
    button.innerHTML = icon() + '<span class="ref-dot" id="refDot" hidden></span>';
    button.addEventListener("click", open);

    // The header is a two-child flexbox with space-between; dropping a third
    // child straight into it would push the existing action off its edge.
    // Grouping the buttons keeps the layout exactly as it was.
    var group = document.createElement("div");
    group.className = "ref-header-actions";
    header.appendChild(group);
    group.appendChild(button);

    Array.prototype.slice
      .call(header.children)
      .filter(function (child) {
        return child !== group && child.classList.contains("icon-btn");
      })
      .forEach(function (child) {
        group.appendChild(child);
      });
  }

  function buildSheet() {
    if (els.overlay) return;

    var overlay = document.createElement("div");
    overlay.className = "ref-overlay";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML =
      '<div class="ref-dialog" role="dialog" aria-modal="true" aria-labelledby="refTitle">' +
        '<div class="ref-handle" aria-hidden="true"></div>' +
        '<div class="ref-head">' +
          '<h2 class="ref-title" id="refTitle">' + t("Invite friends") + "</h2>" +
          '<button type="button" class="icon-btn ref-close" id="refCloseBtn" aria-label="' +
            t("Close") + '">✕</button>' +
        "</div>" +
        '<p class="ref-lead" id="refLead"></p>' +
        '<div class="ref-meter">' +
          '<div class="ref-meter-head">' +
            '<strong id="refUsage"></strong><span id="refNext"></span>' +
          "</div>" +
          '<div class="ref-bar"><div class="ref-bar-fill" id="refBarFill"></div></div>' +
          '<div class="ref-chips"><span class="ref-chip" id="refValid"></span>' +
            '<span class="ref-chip ref-chip-muted" id="refPending"></span></div>' +
        "</div>" +
        '<label class="ref-link-label" for="refLink">' + t("Your invite link") + "</label>" +
        '<input class="ref-link" id="refLink" readonly />' +
        '<div class="ref-actions">' +
          '<button type="button" class="btn-secondary" id="refCopyBtn">' + t("Copy link") + "</button>" +
          '<button type="button" class="btn-primary" id="refShareBtn">' + t("Share link") + "</button>" +
        "</div>" +
      "</div>";

    document.body.appendChild(overlay);

    els.overlay = overlay;
    els.lead = overlay.querySelector("#refLead");
    els.usage = overlay.querySelector("#refUsage");
    els.next = overlay.querySelector("#refNext");
    els.fill = overlay.querySelector("#refBarFill");
    els.valid = overlay.querySelector("#refValid");
    els.pending = overlay.querySelector("#refPending");
    els.link = overlay.querySelector("#refLink");
    els.copy = overlay.querySelector("#refCopyBtn");
    els.share = overlay.querySelector("#refShareBtn");

    if (isFa) overlay.setAttribute("dir", "rtl");

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    overlay.querySelector("#refCloseBtn").addEventListener("click", close);
    els.copy.addEventListener("click", copyLink);
    els.share.addEventListener("click", shareLink);
  }

  /* ── Rendering ──────────────────────────────────────── */

  function render() {
    if (!state || !els.overlay) return;

    els.lead.textContent = t(
      "Every {step} friends who join and save their first event raise your limit by {bonus} events.",
      { step: num(state.step), bonus: num(state.bonus) }
    );

    els.usage.textContent = num(state.used) + " / " + num(state.limit) + " " + t("events used");
    els.next.textContent = state.at_cap
      ? t("You have reached the highest limit. Thank you!")
      : t("{n} more to go for +{bonus} events", {
          n: num(state.invites_to_next),
          bonus: num(state.bonus),
        });

    var ratio = state.limit > 0 ? Math.min(state.used / state.limit, 1) : 0;
    els.fill.style.width = (ratio * 100).toFixed(1) + "%";
    els.fill.classList.toggle("is-hot", ratio >= NEAR_LIMIT_RATIO);

    els.valid.textContent = t("{valid} joined", { valid: num(state.valid_invites) });
    els.pending.textContent = t("{pending} on the way", { pending: num(state.pending_invites) });
    els.pending.hidden = !state.pending_invites;

    els.link.value = state.link;
  }

  function renderNudge() {
    var main = document.querySelector(".app-main");
    var existing = document.getElementById("refNudge");
    if (!main || !state) return;

    var ratio = state.limit > 0 ? state.used / state.limit : 0;
    if (state.at_cap || ratio < NEAR_LIMIT_RATIO) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return;

    var card = document.createElement("section");
    card.className = "ref-nudge";
    card.id = "refNudge";
    card.innerHTML =
      '<div class="ref-nudge-body"><strong>' + t("Running out of space") + "</strong>" +
      "<p>" +
      t("You've used {used} of your {limit} events. Invite friends to get more.", {
        used: num(state.used),
        limit: num(state.limit),
      }) +
      "</p></div>" +
      '<button type="button" class="btn-primary ref-nudge-btn">' + t("Invite now") + "</button>";

    card.querySelector("button").addEventListener("click", open);

    var toolbar = main.querySelector(".toolbar");
    if (toolbar) main.insertBefore(card, toolbar);
    else main.appendChild(card);
  }

  /* ── Actions ────────────────────────────────────────── */

  function open() {
    haptic();
    buildSheet();
    render();
    els.overlay.hidden = false;
    els.overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(function () {
      els.overlay.classList.add("is-open");
    });
    refresh();
  }

  function close() {
    if (!els.overlay) return;
    els.overlay.classList.remove("is-open");
    els.overlay.setAttribute("aria-hidden", "true");
    setTimeout(function () {
      els.overlay.hidden = true;
    }, 200);
  }

  function flash(button, text) {
    var original = button.textContent;
    button.textContent = text;
    setTimeout(function () {
      button.textContent = original;
    }, 1500);
  }

  function copyLink() {
    if (!state) return;
    haptic("success");

    var done = function () {
      flash(els.copy, t("Copied"));
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(state.link).then(done, fallbackCopy);
    } else {
      fallbackCopy();
    }

    function fallbackCopy() {
      try {
        els.link.removeAttribute("readonly");
        els.link.select();
        document.execCommand("copy");
        els.link.setAttribute("readonly", "readonly");
        done();
      } catch (_) {}
    }
  }

  function shareLink() {
    if (!state) return;
    haptic("success");

    var text = t(
      "Join me on TimeManager Pro — never miss a birthday, meeting or deadline again."
    );
    var url =
      "https://t.me/share/url?url=" +
      encodeURIComponent(state.link) +
      "&text=" +
      encodeURIComponent(text);

    if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  /* ── Boot ───────────────────────────────────────────── */

  async function refresh() {
    try {
      state = await fetchState();
      render();
      renderNudge();
    } catch (error) {
      if (els.lead) els.lead.textContent = t("Could not load your invite link.");
    }
  }

  function init() {
    mountHeaderButton();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
