# 0016. The client's address is the rightmost one that is not a proxy's

Status: Accepted
Decided: Batch 27, from a measurement in production. Recorded 2026-09-26 (Batch 27).

## Context
Per-address limits, on public links and on failed authentication, took the
leftmost `X-Forwarded-For` entry, which the client writes, and uvicorn in
production took the same entry as `request.client.host`. One request with a
forged header and a wrong hash showed the chain Render delivers: what the
client wrote, then the client (added by Cloudflare), then a Cloudflare edge
(added by Render), then a Render internal address. A new invented address each
time escaped every per-address limit, and "the rightmost entry" would have
named a Cloudflare edge, putting many users on one budget.

## Decision
`client_ip` is the rightmost entry that is neither in a private, loopback or
link-local network nor in Cloudflare's published ranges (`app/utils/net.py`);
without one, the peer. The failure log names that address beside the chain.

## Consequences
Per-address limits hold against forged headers; the total on failed
authentication (ADR 0015) stays as the second line. The Cloudflare list has to
be kept current: an edge from a new range would be taken for a client until it
is added, so SECURITY.md asks for a check before each release. Should Render
stop using Cloudflare, the rule still finds the client.
