/* One way to open a modal dialog in this app (Batch 20, A11Y-01).

   Every dialog is a native <dialog> opened with showModal(), so the browser
   moves focus inside, makes the page behind it inert and closes it on Escape
   or a back gesture. This module adds what the platform leaves to the page:

   * a stack, so Escape and Telegram's back button close only the topmost
     dialog;
   * focus on the dialog's [autofocus] element when it opens, set explicitly
     because browsers still disagree on the dialog focusing steps;
   * Tab wrapping inside the dialog, since after the last control a browser
     may hand focus to its own chrome instead;
   * focus handed back to whatever opened the dialog once it closes;
   * the page's one status region (#toast) carried into the dialog on top,
     because everything outside a modal dialog is inert, and an inert live
     region is neither shown above the dialog nor announced.

   WebViews without <dialog> (iOS before 15.4) get the same stack, focus and
   Escape rules on the open attribute, without the browser's modality. */
(function () {
  "use strict";

  var TABBABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
  var stack = [];
  var status = null;
  var statusHome = null;
  var statusAfter = null;

  function nativeModal(dialog) {
    return typeof dialog.showModal === "function" && typeof dialog.close === "function";
  }

  function indexOf(dialog) {
    for (var i = stack.length - 1; i >= 0; i -= 1) {
      if (stack[i].dialog === dialog) return i;
    }
    return -1;
  }

  function isOpen(dialog) { return indexOf(dialog) !== -1; }

  // Moved when a dialog opens or closes, never while a message is being
  // written: a screen reader is not handed a live region in the same moment
  // its text changes.
  function placeStatusRegion() {
    if (!status) {
      status = document.getElementById("toast");
      if (!status) return;
      statusHome = status.parentNode;
      statusAfter = status.nextSibling;
    }
    var host = top();
    if (host) {
      if (status.parentNode !== host) host.appendChild(status);
    } else if (status.parentNode !== statusHome) {
      statusHome.insertBefore(status, statusAfter && statusAfter.parentNode === statusHome ? statusAfter : null);
    }
  }

  function top() { return stack.length ? stack[stack.length - 1].dialog : null; }

  // options.focus: element to start at (default: the dialog's [autofocus]).
  // options.onClose(result): runs once, however the dialog was closed.
  // options.requestClose(): the dialog's own close path, for closeTop().
  function open(dialog, options) {
    if (!dialog || isOpen(dialog)) return false;
    options = options || {};
    stack.push({
      dialog: dialog,
      opener: document.activeElement,
      onClose: options.onClose || null,
      requestClose: options.requestClose || null
    });
    if (nativeModal(dialog)) {
      dialog.addEventListener("close", onNativeClose);
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    placeStatusRegion();
    var target = options.focus || dialog.querySelector("[autofocus]");
    if (target && typeof target.focus === "function") target.focus();
    return true;
  }

  // The app's own buttons. Returns false when the dialog was not open.
  function close(dialog, result) {
    var index = indexOf(dialog);
    if (index === -1) return false;
    if (nativeModal(dialog)) {
      if (dialog.open) dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
    finish(index, result);
    return true;
  }

  // The browser closed it (Escape, a back gesture). A "close" event left over
  // from an earlier opening arrives while the dialog is open again: ignored.
  function onNativeClose(event) {
    var dialog = event.currentTarget;
    if (dialog.open) return;
    var index = indexOf(dialog);
    if (index !== -1) finish(index, undefined);
  }

  function finish(index, result) {
    var entry = stack.splice(index, 1)[0];
    entry.dialog.removeEventListener("close", onNativeClose);
    placeStatusRegion();
    var opener = entry.opener;
    if (opener && opener !== document.body && opener.isConnected && typeof opener.focus === "function") {
      opener.focus();
    }
    if (entry.onClose) entry.onClose(result);
  }

  // Escape and Telegram's back button: the topmost dialog only, through its
  // own close path when it has one (the day sheet slides out first).
  function closeTop() {
    if (!stack.length) return false;
    var entry = stack[stack.length - 1];
    if (entry.requestClose) entry.requestClose();
    else close(entry.dialog);
    return true;
  }

  // A click on a native dialog's backdrop is dispatched to the dialog itself,
  // and so is a click on its own padding. Only the coordinates tell them apart.
  function isBackdropClick(dialog, event) {
    if (event.target !== dialog) return false;
    var box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right
      || event.clientY < box.top || event.clientY > box.bottom;
  }

  function tabbables(dialog) {
    return Array.prototype.filter.call(dialog.querySelectorAll(TABBABLE), function (element) {
      return !element.disabled && element.getClientRects().length > 0;
    });
  }

  document.addEventListener("keydown", function (event) {
    if (!stack.length) return;
    var dialog = top();

    if (event.key === "Escape") {
      if (nativeModal(dialog)) return;   // the browser closes it on its own
      event.preventDefault();
      event.stopPropagation();          // before the app's own Escape handling
      closeTop();
      return;
    }
    if (event.key !== "Tab") return;

    var items = tabbables(dialog);
    var active = document.activeElement;
    if (!items.length) {
      event.preventDefault();
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    if (!dialog.contains(active)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && (active === first || items.indexOf(active) === -1)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }, true);

  window.TMModal = {
    open: open,
    close: close,
    closeTop: closeTop,
    isOpen: isOpen,
    top: top,
    isBackdropClick: isBackdropClick
  };
})();
