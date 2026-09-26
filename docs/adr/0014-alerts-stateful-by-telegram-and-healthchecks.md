# 0014. Alerts: once, every six hours, and at the end; healthchecks.io for silence

Status: Accepted
Decided: Batch 25 (Stage 3b), chosen from options. Recorded 2026-09-26 (Batch 25).

## Context
After every run while anything was wrong, the cron endpoint sent the whole
health report, and anything included any failed reminder, however old. With a
cron every minute, that is a message a minute for as long as one user has
blocked the bot, which is how alerts come to be ignored. Nothing noticed a
trigger that stopped running.

## Decision
As chosen: alerts for late or stuck reminders, for failures in the last 24
hours, and for any failed reminder at all; sent when a problem starts, every
six hours while it lasts, and once when it ends. The state lives in the
`alert_state` collection and moves only by atomic updates, so the cron and the
Action, which both check, send each message once. The endpoint pings
healthchecks.io after each run (`HEALTHCHECK_PING_URL`), and `/fail` when a run
breaks; a separate check, a one-minute period and a ten-minute grace, alarms
when the cron falls silent. `POST /tasks/alert-test` fires the path, and
`docs/RUNBOOK.md` says what to do for every reason.

## Consequences
At most four messages a day while a problem lasts, instead of one a minute.
Because any failed reminder counts, one that failed for good keeps the alert
on, repeating every six hours, until its event is edited or deleted; that was
the choice, and the runbook says how to find them. The Action checks too, so an
alert does not depend on the cron existing.
