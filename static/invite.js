/* ──────────────────────────────────────────────────────────────
   invite.js — Batch 12c: joining a shared event.

   Runs once on boot. If the app was opened from a ?startapp=s_<token> link
   it asks what the invite is, shows it, and adds the event only if the
   person says yes. Everything else in the app is untouched.
   ────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var FA = {
    "Shared event": "رویداد مشترک",
    "{name} wants to share this event with you": "{name} می‌خواهد این رویداد را با شما به اشتراک بگذارد",
    "Someone wants to share this event with you": "کسی می‌خواهد این رویداد را با شما به اشتراک بگذارد",
    "Add to my events": "به رویدادهای من اضافه کن",
    "Not now": "الان نه",
    "Added. It stays in step with the original.": "اضافه شد. با نسخهٔ اصلی همگام می‌ماند.",
    "You already have this one.": "این را از قبل داری.",
    "This is your own event.": "این رویداد خودت است.",
    "Your event limit is full.": "سقف رویدادهایت پر است.",
    "Could not add it.": "اضافه نشد.",
  };

  var isFa = String(
    (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || ""
  ).toLowerCase().indexOf("fa") === 0;

  function t(text, vars) {
    var out = isFa && FA[text] ? FA[text] : text;
    if (vars) {
      Object.keys(vars).forEach(function (k) { out = out.split("{" + k + "}").join(vars[k]); });
    }
    return out;
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  async function api(path, body) {
    var response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ initData: (tg && tg.initData) || "" }, body)),
    });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(String(data.detail || response.status));
    return data;
  }

  function close(overlay) {
    overlay.classList.remove("is-open");
    setTimeout(function () { overlay.remove(); }, 200);
  }

  function show(invite, token) {
    var overlay = document.createElement("div");
    overlay.className = "invite-overlay";
    if (isFa) overlay.setAttribute("dir", "rtl");

    var who = invite.from_name
      ? t("{name} wants to share this event with you", { name: invite.from_name })
      : t("Someone wants to share this event with you");

    overlay.innerHTML =
      '<div class="invite-card" role="dialog" aria-modal="true">' +
        (invite.card_url
          ? '<img class="invite-image" alt="" src="' + invite.card_url + '" />'
          : "") +
        '<span class="invite-tag">' + t("Shared event") + "</span>" +
        '<p class="invite-who"></p>' +
        '<h3 class="invite-title"></h3>' +
        '<p class="invite-date"></p>' +
        '<p class="invite-error" id="inviteError" hidden></p>' +
        '<div class="invite-actions">' +
          '<button type="button" class="btn-secondary" id="inviteNo"></button>' +
          '<button type="button" class="btn-primary" id="inviteYes"></button>' +
        "</div>" +
      "</div>";

    document.body.appendChild(overlay);
    overlay.querySelector(".invite-who").textContent = who;
    overlay.querySelector(".invite-title").textContent = invite.title || "";
    overlay.querySelector(".invite-date").textContent =
      isFa ? (invite.date_jalali || invite.date_iso || "") : (invite.date_iso || "");
    overlay.querySelector("#inviteNo").textContent = t("Not now");
    overlay.querySelector("#inviteYes").textContent = t("Add to my events");

    requestAnimationFrame(function () { overlay.classList.add("is-open"); });

    overlay.querySelector("#inviteNo").addEventListener("click", function () {
      close(overlay);
    });

    overlay.querySelector("#inviteYes").addEventListener("click", async function () {
      var yes = overlay.querySelector("#inviteYes");
      var problem = overlay.querySelector("#inviteError");
      yes.disabled = true;

      try {
        var result = await api("/api/group/join", {
          token: token,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        });
        haptic("success");
        close(overlay);
        if (window.TMApp && window.TMApp.reload) window.TMApp.reload();
        if (result.already_joined) alert(t("You already have this one."));
      } catch (error) {
        var code = String(error.message || "");
        problem.textContent =
          code.indexOf("EVENT_LIMIT_REACHED") >= 0 ? t("Your event limit is full.")
          : code.indexOf("ALREADY_YOURS") >= 0 ? t("This is your own event.")
          : t("Could not add it.");
        problem.hidden = false;
        yes.disabled = false;
      }
    });
  }

  async function init() {
    var param = String((tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "");
    if (param.toLowerCase().indexOf("s_") !== 0) return;

    var token = param.slice(2);
    try {
      var invite = await api("/api/group/invite", { token: token });
      // Opening your own invite link is not an error, it just has nothing to
      // offer — the event is already there.
      if (!invite.is_own) show(invite, token);
    } catch (_) {
      /* A dead or malformed link simply opens the app as normal. */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
