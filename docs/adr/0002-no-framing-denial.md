# 0002. The app does not deny framing

Status: Accepted
Decided: Batch 19. Recorded 2026-09-26 (Batch 22).

## Context

`X-Frame-Options` or CSP `frame-ancestors` is the usual clickjacking control.
A Telegram Mini App, though, is loaded inside Telegram's own frame, and on the
web and desktop clients a framing denial stops it from opening at all.

## Decision

Neither header is set (app/middleware.py says so where it would be). Naming the
Telegram origins instead was considered and rejected: the set differs across
clients and versions, and a wrong list fails closed on someone's phone, where
nobody sees a console warning.

## Consequences

Framing denial is not available as a defence. What stands in its place: every
state-changing request carries Telegram `initData`, verified by HMAC, rather
than a cookie a framing page could ride on; destructive actions ask first
(the confirm dialog of ADR 0006); and the rest of the CSP stays strict.
