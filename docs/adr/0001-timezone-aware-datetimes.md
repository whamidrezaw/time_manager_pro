# 0001. Datetimes are timezone-aware from the database on

Status: Accepted
Decided: Batch 19. Recorded 2026-09-26 (Batch 22).

## Context

The Motor client returned naive datetimes. A naive datetime passed to
`.astimezone()` is read as the server's local time, which breaks the rule in
docs/DEBUGGING.md that the database holds UTC the moment a host is not UTC:
reminder times shifted by the host's offset.

## Decision

The client is created with `tz_aware=True` (app/db.py). Every datetime read
back is UTC-aware, and code converts with `as_utc`. The test harness uses the
same setting (`AsyncMongoMockClient(tz_aware=True)`).

## Consequences

Comparisons and arithmetic mean the same on any host. Mixing in a naive
datetime now raises a `TypeError` instead of shifting a reminder silently,
which is the point: the bug becomes loud.
