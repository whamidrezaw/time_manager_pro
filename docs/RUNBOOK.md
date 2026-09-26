# Runbook

What to do when an alert arrives (ADR 0014). An alert names its reasons, and
each reason has a section here. While any reason holds, the alert repeats
every six hours; a "back to normal" message follows when none does.

## Test-firing

After every deploy that touches alerting, fire the path once (PowerShell):

    Invoke-RestMethod -Method Post -Uri https://timemanager-pro.onrender.com/tasks/alert-test -Headers @{ "X-Tasks-Secret" = "<TASKS_SECRET>" }

It answers `telegram: True, healthchecks: True`, and "Test alert" arrives in
the admin chat. `telegram: False`: `ADMIN_CHAT_ID` or `BOT_TOKEN` is wrong.
`healthchecks: False`: `HEALTHCHECK_PING_URL` is unset or wrong.

## late

A pending reminder is more than `OVERDUE_AFTER_MINUTES` (10) past its time:
the runs have stopped, or cannot keep up.

1. Render, Logs: search `"event": "reminder_run"`. One line a minute means the
   cron runs; none means it does not.
2. The cron service: the last calls to `/tasks/run-reminders`. 401 or 503:
   `TASKS_SECRET` differs or is unset. 5xx: search Render's logs for
   `"event": "request"` with `"status": 500` at that time.
3. healthchecks.io: is the cron check down too (below)?
4. GitHub, Actions, the reminder workflow: the fallback still runs, slowly.

Once a trigger runs again, the next run sends what is overdue, each occurrence
once (ADR 0004).

## stuck

A run claimed reminders and died before it finished. Every call to the
endpoint first puts such claims back, so this lasting means the endpoint is not
being called: check as for late.

## failed_24h

Reminders that failed in the last 24 hours: Telegram refused them for good,
most often because the user blocked the bot or the chat is gone. Many at once
point at the bot token or at Telegram itself, not at users: search Render's
logs around that time.

## failed

Every failed reminder, however old (chosen in Batch 25). A reminder stays
failed until its event is edited, which schedules it afresh, or deleted; so
while one exists this alert stays on and repeats every six hours. To find them:
MongoDB Atlas, the events collection, filter `{ "notify_status": "failed" }`.

## The cron check is down

healthchecks.io has not heard from `POST /tasks/run-reminders` for longer
than the check's grace time. A `/fail` ping means a run started and broke:
Render's logs have the traceback.

1. Render: is the service live? A failed deploy leaves the previous one
   running; a crash shows under Events.
2. The cron service: is the job on, and what did its last calls return?
3. `TASKS_SECRET` must be the same on Render and in the cron job.

## Rolling back

Render, the service, Events: redeploy the last good deploy. Or revert the merge
commit on GitHub and let the auto-deploy run. For the Start Command (ADR 0013),
put the previous command back in Settings.
