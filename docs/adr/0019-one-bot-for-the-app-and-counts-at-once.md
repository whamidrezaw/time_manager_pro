# 0019. One bot for the app, and the health counts at once

Status: Accepted
Decided: Batch 31, from production measurements (Batch 30). Recorded 2026-09-27 (Batch 31).

## Context
Batch 30 measured production. Every database command cost about 145 ms, the
distance between Render and the database, and `measure()` sent its seven one
after another: about a second of each reminder run. Starting a bot, a new
connection and `getMe`, took 0.8 to 1 s, and every webhook update, reminder run
and background message started one, so each bot reply took 1.6 to 2 s.

## Decision
`measure()` sends its seven commands together. The app starts one bot at
start-up, with eight connections so senders at the same moment do not queue,
and every sender uses it through `telegram_bot()`; without it (tests, the
Action's run) a sender starts its own from its module's `Bot`, as before. A
check for due reminders before starting a bot was considered and dropped: the
shared bot already removes that cost, and the check would add a round trip.

## Consequences
A reminder run and each bot reply lose about 0.8 s, and Telegram sees one
`getMe` per start-up instead of one a minute. The largest cost left is the
145 ms per database command, which only running the service and the database
in one region removes: a deployment choice, not code.
