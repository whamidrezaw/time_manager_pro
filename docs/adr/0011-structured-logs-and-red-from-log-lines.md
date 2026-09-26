# 0011. Logs are JSON lines, and RED comes from them

Status: Accepted
Decided: Batch 23 (Stage 3), chosen from options. Recorded 2026-09-26 (Batch 23).

## Context
The logs were plain text with no request id, so one user's problem could not
be followed, and nothing measured rate, errors or duration. The observability
checklist asks for structured logs, RED numbers per endpoint and dependency,
and requests that can be traced from start to end.

## Decision
Every line is one JSON object (`app/observability.py`) in Render's own log
stream; `LOG_FORMAT=text` keeps it readable on a laptop. Every request gets an
id, a valid incoming `X-Request-ID` or a new one, which is on every line it
logs and on its response. Each request writes one line with its route
template (never a token from the path), status and duration; each reminder run,
from the cron endpoint or the Action, writes one summary line. Rate, errors and
duration are read from those lines, not from a metrics server.

## Consequences
Nothing new to run or pay for, and a line can be searched by `request_id`,
`event` or `route`. Render keeps logs for a limited time, so a longer history
would need a log service later. Alerts are the other half (ADR 0014).

Amended in Batch 26: production showed uvicorn's access lines, raw paths and
client addresses, beside the RED lines. The gunicorn worker sets the access
logger's handlers and level again after the app is imported, so a level
alone was undone; a filter on that logger now drops them.
