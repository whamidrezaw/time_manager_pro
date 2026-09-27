# Changelog

Every release of TimeManager Pro, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The reasons behind the changes
are in `docs/adr/`; earlier history is in `git log`.

## [1.0.0] - 2026-09-27

The first release: Batches 19 to 32, from the review that found two
data-loss bugs to a measured, audited and documented service.

### Added
- Accessibility to WCAG 2.1 AA: every dialog on one modal stack, date fields
  that open from the keyboard, a name, role and state for every control, a
  list with structure, errors that stay on their field, readable colours in
  every theme, and a countdown page and share cards that name themselves
  (ADR 0006, ADR 0008).
- Admin control over event limits from the bot: `/limits`, `/limit`,
  `/setbase`, `/setbonus`, `/setstep`, and `/logid` to find a user in the logs
  (ADR 0009, ADR 0018).
- `/deletemydata`: erasure on request, confirmed by a button only the sender
  can press (ADR 0017).
- Observability: JSON logs with a request id, one RED line per request with
  its time to response and database cost, the phases of each reminder run,
  and alerts by Telegram with a healthchecks.io ping (ADR 0011, ADR 0014);
  `docs/RUNBOOK.md` for every alert.
- `SECURITY.md`, `CONSTRAINTS.md`, a Definition of Done, and nineteen
  architecture decision records.

### Changed
- The base limit is 25, and the limit nudge appears only at the limit.
- Saving an event no longer waits for Telegram: the confirmation follows the
  response.
- A compact top card leaves room for two events above the tab bar.
- Production starts through the `uvicorn-worker` package (ADR 0013).
- One bot for the whole app, and the health counts sent at once (ADR 0019).
- Logs name no one: ids become keyed pseudonyms, and titles and dates stay
  out (ADR 0018).

### Fixed
- Two data-loss bugs: events deleted by the TTL after an edit and after a
  worker outage (ADR 0003).
- Duplicate reminders (ADR 0004), an unrecoverable failed state, and
  timezone-naive dates (ADR 0001).
- Confirmations that could fire twice.
- A user restricted in a group could link it after leaving it.
- uvicorn's access lines put tokens and addresses in the logs.
- Tests that failed under load now wait for causes, not clocks (ADR 0010).

### Security
- Unauthenticated requests are bounded: bodies at most 64 KB, `initData` at
  most 8192 characters, failed authentication throttled per address and in
  total (ADR 0015).
- The client's address is taken past Cloudflare and Render's proxies, so it
  cannot be forged (ADR 0016).
- Rate limits on public routes, constant-time secret comparisons, a
  `Permissions-Policy`, a logged CSP override, and no part of a hash in logs.
- `pip-audit` finds no known vulnerability (2026-09-27).

### Removed
- The long-running reminder worker and its deployment files: reminders are
  sent by a cron endpoint, with the GitHub Action as fallback (ADR 0005,
  ADR 0012).
- Dead code, including `app/deps.py`.

### Known debt
- Two long functions and the 2586-line `static/app.js`, left for after this
  release on purpose (see "Known debt" in `CONSTRAINTS.md`).

[1.0.0]: https://github.com/whamidrezaw/time_manager_pro/releases/tag/v1.0.0
