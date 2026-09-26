# 0003. Garbage collection waits a full period plus thirty days

Status: Accepted
Decided: Batch 19. Recorded 2026-09-26 (Batch 22).

## Context

Events have a TTL index on `expire_at`. The old daily value was 2 days, and
only a successful send pushed it forward, so a worker outage longer than the
gap let the index delete users' events; an event edited from recurring to
one-off also kept its old `expire_at` and was deleted while still live.

## Decision

`expire_for_repeat` (app/utils/dates.py) gives one full period plus at least
thirty days after the next reminder: 31 days for daily, 37 weekly, 61 monthly,
400 yearly. Only recurring series get an `expire_at`; a one-off keeps its
record, and an edit into a one-off removes the field.

## Consequences

In the code's words, collection is housekeeping; it must never be the thing
that notices the worker is down. A worker down for weeks loses nothing. The
database keeps a little more, and noticing an outage is the job of
observability (the Stage 3 alert), not of data loss.
