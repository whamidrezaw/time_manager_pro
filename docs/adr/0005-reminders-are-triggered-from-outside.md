# 0005. Reminders are triggered from outside, every minute

Status: Accepted
Decided: Batch 18; measured in Batch 20. Recorded 2026-09-26 (Batch 22).

## Context

The web service runs no background loop of its own. The GitHub Actions
schedule is best effort: GitHub documents that scheduled runs are delayed, and
some dropped, under load. Measured in Batch 20: 100 runs over 15 days, a median
gap of 212 minutes (106 to 409), where 5 were configured.

## Decision

`POST /tasks/run-reminders` is the trigger, called every minute by an external
cron. It needs `X-Tasks-Secret` (constant-time compare) and is closed while
`TASKS_SECRET` is unset. The GitHub Action stays as a slow fallback; the two
are safe together (ADR 0004). `POST /tasks/health` reports how many reminders
are overdue and by how much.

## Consequences

Punctuality depends on the external cron existing and succeeding, so it has to
be checked in production and alerted on (Stage 3). With an empty queue,
`overdue: 0` proves nothing (docs/DEBUGGING.md). The long-running
`worker/reminder_worker.py` and its deploy files are superseded, and were
removed in Batch 24 (ADR 0012).
