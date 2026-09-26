# 0006. Every dialog is a native dialog on one modal stack

Status: Accepted
Decided: Batch 20 (D5, D6, D7). Recorded 2026-09-26 (Batch 22).

## Context

Nine dialogs were overlays: focus escaped behind them, Escape worked in some,
a dialog on top of another closed the wrong one, and a cancelled confirm left
its handler armed, so a later confirm sent two DELETE requests (A11Y-01, 02).

## Decision

Every modal is a `<dialog>` opened with `showModal()` through `static/modal.js`
(TMModal): one stack, so Escape and Back close only the top one; focus starts on
the title (a confirm starts on Cancel) and returns to the opener; the status
region follows the top dialog. Sheets sit in zero-size dialog hosts so they
keep their look.

## Consequences

The browser supplies inertness and the focus trap. WebViews without `<dialog>`
(iOS before 15.4) keep working through z-index fallbacks. A new dialog goes
through TMModal; the dialog tests hold all nine to the same rules.
