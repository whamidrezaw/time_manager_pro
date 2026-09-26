# 0013. Production starts through the uvicorn-worker package

Status: Accepted
Decided: Batch 25, chosen from options. Recorded 2026-09-26 (Batch 25).

## Context
Render starts the app with `gunicorn -k uvicorn.workers.UvicornWorker`. In the
pinned uvicorn (0.30.6) that module is deprecated in favour of the separate
`uvicorn-worker` package, and an upgrade that drops it would stop production
at start-up. No test starts gunicorn, so the suite never saw the warning.

## Decision
`uvicorn-worker==0.3.0` is pinned: 0.4.0 needs uvicorn 0.36 or later, an
upgrade of its own. The Start Command becomes
`gunicorn -k uvicorn_worker.UvicornWorker app.main:app`, set in Render's
settings only after the pin is deployed, so the package is there when the new
command runs. Plain uvicorn without gunicorn was the other option; it keeps one
dependency fewer but gives up gunicorn's process management.

## Consequences
The same server and behaviour, through a supported path. The command lives in
Render's settings, not in the repository, so the README states it and
`tests/test_start_command.py` checks the README, the pin, and that the class
loads without a deprecation (on Linux; gunicorn does not import on Windows).
Rolling back is restoring the old Start Command.
