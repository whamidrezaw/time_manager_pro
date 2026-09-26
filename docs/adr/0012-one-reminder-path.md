# 0012. One reminder path: the endpoint, with the Action as fallback

Status: Accepted
Decided: Batch 24, after Render's settings were read. Recorded 2026-09-26 (Batch 24).

## Context
Reminders used to be sent by a long-running worker (`worker/reminder_worker.py`),
deployed by a `Dockerfile` and `fly.toml` to Fly.io, with systemd units for a
VPS. ADR 0005 replaced it with an endpoint called by a cron and the GitHub
Action as fallback, but the old files stayed. Render's settings show the web
service runs as native Python (`pip install -r requirements.txt`, then
`gunicorn -k uvicorn.workers.UvicornWorker app.main:app`), so nothing built
the `Dockerfile`, and the README described a deployment that did not exist.

## Decision
The worker, `run-worker.sh`, the `Dockerfile`, `.dockerignore`, `fly.toml`,
the systemd units and the `REMINDER_POLL_INTERVAL_SECS` setting are removed.
The README describes the deployment that runs. `tests/test_one_reminder_path.py`
keeps them gone and nothing pointing at them; only these records may name them.

## Consequences
One way a reminder is sent, to reason about and to monitor (ADR 0011). The 51
lines no test could reach no longer count against coverage, so the floor rises.
If a long-running worker is ever needed again, git history keeps this one. A
Fly.io app deployed earlier would keep running old code until it is shut down
there: removing its files does not stop it.
