# Releasing a version

A version is released once every change in it is merged, and only when the
list below holds. The tag names the commit that production runs.

## Before the tag

For 1.0.0, each checked on 2026-09-27 unless it says otherwise:

- [x] CI is green on `main`, and the known-failures list is empty.
- [x] The whole suite passes with no deprecation warnings, above the
      coverage floor in `CONSTRAINTS.md` (83.96 % against 77 %).
- [x] `pip-audit -r requirements.txt` finds no known vulnerability.
- [x] Cloudflare's ranges in `app/utils/net.py` match
      https://www.cloudflare.com/ips/ (unchanged since 2023-09-28).
- [x] The alert path was test-fired in production (Batch 25).
- [x] Security audit (Batch 27), five-axis review (Batch 29) and
      performance review (Batches 30 and 31) are done; what waits is under
      "Known debt" in `CONSTRAINTS.md`.
- [ ] A screen-reader pass with TalkBack on Android, by hand.
- [x] `CHANGELOG.md` has the version, dated, and `app/__init__.py` says the
      same (a test checks the two).

## Tagging

From an up-to-date `main`, after the last pull request is merged:

    git switch main
    git pull --ff-only
    git tag -a v1.0.0 -m "TimeManager Pro 1.0.0"
    git push origin v1.0.0

Then, on GitHub, create a release from the tag and paste its section of
`CHANGELOG.md` as the notes.

## After the deploy

- Render's log shows `Starting TimeManager Pro 1.0.0 (production)`.
- Open the Mini App, add an event, and see it in the list.
- An event with a reminder three minutes ahead sends it on time.
- `POST /tasks/alert-test` answers `telegram: True, healthchecks: True`
  (`docs/RUNBOOK.md`, Test-firing).

## Rolling back

Render, the service, Events: redeploy the last good deploy. For a longer way
back, revert the merge commit on GitHub and let the auto-deploy run. A tag is
never moved or reused: a fix ships as the next patch version, `1.0.1`.
