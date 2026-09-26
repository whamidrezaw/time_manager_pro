# 0015. Unauthenticated requests are bounded

Status: Accepted
Decided: Batch 27, from the Stage 5 security audit, chosen from options. Recorded 2026-09-26 (Batch 27).

## Context
The audit (SECURITY.md) found that a request was read and parsed whole before
its `initData` could be refused: no limit on the body, no bound on `initData`,
and no throttle on failed authentication, while production runs one worker.
The client's address comes from `X-Forwarded-For`, which a client can forge.

## Decision
A body over `MAX_REQUEST_BYTES` (64 KB) gets 413 before it is read whole; with
no `Content-Length` the bytes are counted as they arrive. `initData` is at most
8192 characters. A failed authentication counts per address (30 a minute) and
in total (300 a minute), in memory, and past either the answer is 429. The
total goes beyond the audit's proposal because a per-address limit yields to
invented addresses; a real user never fails, so it never limits one. The
failure log keeps the address and the forwarded chain, and no part of a hash.

## Consequences
A flood costs at most 64 KB of reading and one hash per request, soon meets a
429, and cannot move to the database. With invented addresses an attacker can
use up the total and make other failed logins wait a minute, which only bots
and mistakes see. The largest real request, an event with a 2000-character
note, stays under 16 KB.
