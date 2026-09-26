# Security

## Reporting a vulnerability

Please report it privately: on GitHub, **Security → Report a vulnerability**
(private vulnerability reporting), not in a public issue. Say what you found,
how to reproduce it and what it affects; you will get an answer within a week.

## Trust boundaries

| Boundary | On the other side | Defences |
|---|---|---|
| Mini App API (`/api/*`) | anyone with the app | Telegram `initData` checked by HMAC-SHA256 (key `HMAC("WebAppData", bot token)`, constant-time compare), valid for 15 minutes; every event query filtered by its owner; rate limits per user and scope; bodies at most 64 KB and `initData` at most 8192 characters; failed authentication throttled, 30 a minute per address and 300 in total (ADR 0015) |
| Telegram webhook | Telegram | secret token header, constant-time compare, and no start-up without it; admin commands only from `ADMIN_CHAT_ID` as the sender |
| Task endpoints (`/tasks/*`) | the cron and the Action | `X-Tasks-Secret`, constant-time compare; closed while `TASKS_SECRET` is unset |
| Public links (`/c/<token>` and cards) | anyone | 128-bit tokens; a rate limit per address; only title, date and countdown shown; rendering off the event loop |
| The page | the browser | a strict CSP with no inline scripts; user text inserted as text, never as HTML; `Permissions-Policy` turns off camera, microphone, location, payment and USB; HSTS, `nosniff`, `Referrer-Policy`, CORP |
| Logs | Render | JSON lines; no tokens (route templates, uvicorn's access lines dropped); no part of any hash; httpx's URL lines quiet; user and chat ids as keyed pseudonyms, and no titles or dates (ADR 0018) |

## Dependencies

Every dependency is pinned in `requirements.txt`. `pip-audit` found no known
vulnerability on 2026-09-26 (Batch 27); run `pip-audit -r requirements.txt`
before each release.

## Accepted risks

- **No framing denial.** Telegram loads the app in a frame (ADR 0002).
  State-changing requests need `initData`, and destructive ones a confirmation.
- **The client's address rests on Cloudflare's published ranges.** It is the
  rightmost `X-Forwarded-For` entry that is neither internal nor a Cloudflare
  edge (ADR 0016). An edge from a range Cloudflare adds later would be taken for
  a client until `app/utils/net.py` lists it, putting the users behind it on one
  budget: check https://www.cloudflare.com/ips/ before each release.
- **Referral bonuses can be farmed** with several Telegram accounts; every
  limit stops at `MAX_EVENTS_PER_USER`.
- **Failed authentications log the address and the forwarded chain**, as
  security events, for as long as Render keeps logs.

## Erasure on request

Anyone can send `/deletemydata` to the bot in a private chat (ADR 0017). It
asks first, with a button that only the sender can press and that expires
after ten minutes. Then everything the app keeps about that person is erased:
their events with the public and join links on them, their user and referral
record, their username, a limit the admin set for them, their rate-limit
counters and their membership of group chats. Invitees stop pointing at them,
and the admin's audit trail keeps what was done with the target replaced.

What stays belongs to others or lies outside the app: a group chat's record,
other members' own copies of a shared event, messages the bot already sent in
Telegram, and Render's logs until they expire.

## How the client's address is found

Measured on 2026-09-26 with one request carrying a forged header and a wrong
hash, `X-Forwarded-For` arrives as

    <what the client wrote>,<the client, added by Cloudflare>, <a Cloudflare edge, added by Render>, <a Render internal address>

Both the old `client_ip` and uvicorn took the leftmost entry, the forged one.
The client is the rightmost entry that is neither internal nor a Cloudflare
edge (`app/utils/net.py`, ADR 0016); whatever a client writes sits to its left.
To check again, send the same request (PowerShell; the 403 shows as an error)
and read `forwarded=` in the line `Bad Telegram initData HMAC`:

    Invoke-RestMethod -Method Post -Uri https://timemanager-pro.onrender.com/api/list -ContentType "application/json" -Headers @{ "X-Forwarded-For" = "203.0.113.7" } -Body '{"initData": "auth_date=1&hash=00"}'
