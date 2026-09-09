# Debugging TimeManager Pro

A prompt and a procedure. Paste the top section into an AI assistant together
with your symptom; work through the rest yourself.

---

## The prompt

> You are helping me debug **TimeManager Pro**, a Telegram Mini App and bot.
> Read this description before proposing anything.
>
> **Shape of the system.** A FastAPI app on Render serves both the Telegram
> webhook and the Mini App (`/webapp`). MongoDB Atlas holds `events`, `users`
> and `chats`. Reminders are sent by `process_due_reminders`, triggered two
> ways: `POST /tasks/run-reminders` from an external cron, and a GitHub
> Actions schedule as a fallback. Front end is vanilla JavaScript with no
> build step: `app.js` plus `referral.js`, `share.js`, `views.js` and
> `invite.js`, which talk to it through `window.TMApp` and `window.TMShare`.
>
> **Rules I hold you to.**
> 1. Do not propose a fix before you can name the mechanism. "Try adding a
>    delay" is not a diagnosis.
> 2. Occurrence and reminder maths lives in `app/utils/dates.py` and nowhere
>    else. If a second implementation would fix it, the fix is wrong.
> 3. Every event belongs to a `user_id`; every query that touches one event
>    filters on `{_id, user_id}` together. A fix that drops that is a data
>    leak, not a fix.
> 4. Timezones: events store `date_iso` plus `tz_name`, and everything is
>    computed in the event's own zone, never the server's. `datetime.now()`
>    without a timezone is a bug.
> 5. The front end has no build step and no framework. Do not introduce one.
> 6. Changes arrive as a single self-applying Python script with exact
>    anchors, a `--check` mode, and no writes unless every step resolves.
> 7. Tell me what you are unsure about. A confident wrong cause costs more
>    than an honest "I need to see X".
>
> **My symptom:** …
>
> **What I have already checked:** …

---

## Before you debug anything

Reproduce it, and write down the smallest input that shows it. Half the
reports in this project turned out to be two different behaviours wearing the
same description — "the repeat is broken" meant *repeating reminders before
the event*, which the app had never had.

## Where things break, in order of likelihood

**1. The reminder never arrived.**
Check punctuality first, not the code:

```bash
curl -s -X POST -H "X-Tasks-Secret: $TASKS_SECRET" https://YOUR_HOST/tasks/health
```

`overdue: 0` means the queue is moving and the problem is one event, not the
worker. A non-zero `overdue` with a large `worst_late_minutes` means the
trigger is not firing — check the cron, then the GitHub Action.

Then look at the event itself: `notify_status` should be `pending` with a
`next_notify_at` in the future. `done` with a repeat set means the series
ended; `processing` for more than fifteen minutes means a run died mid-send
and `recover_stale_processing` is not running.

**2. The reminder arrived at the wrong time.**
Almost always a timezone. Compare `tz_name` on the event with the timezone
the browser reported when it was created. `first_schedule` computes in the
event's zone; if the zone is wrong, everything downstream is consistently
wrong by the same offset — which is the tell.

**3. The Mini App shows stale content.**
`asset_version` is a hash of the static files computed at boot. If Render has
not redeployed, the old hash is still being served and the browser is right
to use its cache. Check that the new code is actually live:

```bash
curl -s https://YOUR_HOST/static/app.js | grep -c "SOMETHING_FROM_YOUR_CHANGE"
```

**4. The calendar shows the wrong days.**
`expand_occurrences` walks the series with `advance_occurrence` from
`app/utils/dates.py`. If the calendar and the reminders disagree, one of them
stopped using that function — that is the bug, not the dates.

**5. Something works for you and not for another account.**
Ownership. Every read of a single event filters on `user_id` as well as
`_id`. A shared event has a member copy per user; check `share_role` before
concluding anything about "the" event.

**6. A deep link does nothing.**
Four kinds share one entry point: `r_` referral, `e_` public countdown,
`s_` shared event, `g_` chat destination. Each parser rejects the other
three by design. A link that silently does nothing is usually the right
prefix handled by the wrong file.

## Tools

```bash
ruff check . && pytest              # 229 tests; run before you look anywhere else
python -m pytest tests/test_dates.py -q     # the maths, in isolation
curl -s -X POST -H "X-Tasks-Secret: $TASKS_SECRET" https://YOUR_HOST/tasks/run-reminders
```

Render's dashboard has the live logs. Every module logs under `tm_pro.*`, so
`tm_pro.reminders`, `tm_pro.cards`, `tm_pro.share` and `tm_pro.chats` narrow
a search quickly.

## The rule that has paid off most

When a symptom and an explanation both sound right, find the one measurement
that separates them before writing any code. The reminders being late looked
like a bug in the scheduling maths for weeks; one look at the *run history*
rather than the code showed the runs themselves were arriving late.
