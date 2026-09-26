# 0004. Each reminder occurrence has an idempotency key

Status: Accepted
Decided: Batch 19. Recorded 2026-09-26 (Batch 22).

## Context

A reminder is sent, then its event is updated. A crash or timeout between the
two sent the same occurrence again on the next run: duplicate reminders.

## Decision

A run claims each due event atomically (`find_one_and_update` to
`notify_status: processing`). The occurrence being sent is keyed by its
`next_notify_at`, stored as `last_sent_key`; an occurrence whose key is
already stored is never sent again, only moved on. A claim left in
`processing` by a crash is recovered after `STALE_PROCESSING_SECS`.

## Consequences

Two triggers may run at the same time without sending anything twice, which is
what lets the GitHub Action stay as a fallback beside the cron (ADR 0005).
The key is the occurrence's own time, so an edit that moves a reminder gives
it a new key, and the changed reminder is sent.
