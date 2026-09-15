// Lifted out of templates/countdown.html so the page needs no
// script-src 'unsafe-inline'.
//
// The destination now arrives through a data attribute rather than being
// interpolated into a JS string literal. Jinja autoescapes for HTML, not for
// JavaScript, so `"{{ miniapp_url }}"` inside a script was relying on the
// value never containing a quote. It never does today, but the HTML attribute
// path is escaped properly by the template engine.
//
// Telegram's Android in-app browser identifies itself; iOS usually does not.
// So this is an opportunistic shortcut, never the only way through — the
// button on the page is what everyone else uses.
(function () {
  var target = document.body.getAttribute("data-miniapp-url");
  if (target && /Telegram/i.test(navigator.userAgent)) {
    window.location.replace(target);
  }
})();
