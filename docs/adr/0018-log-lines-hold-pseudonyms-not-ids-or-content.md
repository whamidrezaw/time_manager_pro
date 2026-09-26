# 0018. Log lines hold pseudonyms, not ids or what users wrote

Status: Accepted
Decided: Batch 28, chosen from options (B). Recorded 2026-09-26 (Batch 28).

## Context
Production logs showed `add_event user_id=... title='testi' date=...`: a title
is whatever the user wrote, and about thirty log calls named user or chat ids.
`/deletemydata` cannot reach Render's logs. A Telegram id is a number of about
ten digits, so a plain hash of it is reversed by hashing every candidate.

## Decision
No log call writes an event's title or date. Every log record is made through
a record factory that replaces each argument that looks like a Telegram id, six
or more digits and negative for groups, with a pseudonym: `u:` and twelve hex
digits of an HMAC keyed with a secret derived from the bot token. The factory
runs before any handler, so every output gets the same, and a log call added
later is covered without being touched. `/logid <id or @username>` gives the
admin the pseudonym to search for.

## Consequences
Logs still follow one user through a request and across runs, without naming
them. A pseudonym changes when the bot token does. A timestamp of six or more
digits in an argument is replaced too; the failure log's `auth_date` is the one
such case, and nothing reads it. A test fails if a log call writes a title again.
