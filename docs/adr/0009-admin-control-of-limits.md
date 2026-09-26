# 0009. Limits are managed from the bot, by one admin

Status: Accepted
Decided: Batch 20 (D13). Recorded 2026-09-26 (Batch 22).

## Context

The owner wanted to change the event limit and give single users a limit of
their own, or none, without a redeploy, and to be unlimited himself.

## Decision

The admin is `ADMIN_CHAT_ID` from the environment; no command grants the role.
Bot commands (`/limits`, `/limit`, `/setbase`, `/setbonus`, `/setstep`) are
validated, audited in `admin_audit` and confirmed. Overrides, usernames and the
runtime settings live in collections of their own, never on the user document
the referral logic reads. Unlimited stops at `MAX_EVENTS_PER_USER`.

## Consequences

No redeploy for a limit change, and every change can be traced. A `@username`
resolves only once that user has been seen; the numeric id always works. One
function, `effective_event_limit`, answers every limit check.
