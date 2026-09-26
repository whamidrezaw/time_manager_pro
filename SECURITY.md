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
| Logs | Render | JSON lines; no tokens (route templates, uvicorn's access lines dropped); no part of any hash; httpx's URL lines quiet |

## Dependencies

Every dependency is pinned in `requirements.txt`. `pip-audit` found no known
vulnerability on 2026-09-26 (Batch 27); run `pip-audit -r requirements.txt`
before each release.

## Accepted risks

- **No framing denial.** Telegram loads the app in a frame (ADR 0002).
  State-changing requests need `initData`, and destructive ones a confirmation.
- **The client's address can be forged.** It comes from `X-Forwarded-For`, so
  per-address limits are a first line only; the total on failed authentication
  holds against invented addresses. Open until the check below is done.
- **Referral bonuses can be farmed** with several Telegram accounts; every
  limit stops at `MAX_EVENTS_PER_USER`.
- **Failed authentications log the address and the forwarded chain**, as
  security events, for as long as Render keeps logs.

## Open: how Render's proxy builds X-Forwarded-For

Send one request with a forged header and a wrong hash (PowerShell; it answers
403, which PowerShell shows as an error):

    Invoke-RestMethod -Method Post -Uri https://timemanager-pro.onrender.com/api/list -ContentType "application/json" -Headers @{ "X-Forwarded-For" = "203.0.113.7" } -Body '{"initData": "auth_date=1&hash=00"}'

In Render's logs, the line `Bad Telegram initData HMAC` shows `forwarded=`:

- `203.0.113.7, <your address>`: the proxy appends. The address it added is the
  rightmost one, and `client_ip` should take that one.
- only `<your address>`: the proxy overwrites, and the header cannot be forged.
