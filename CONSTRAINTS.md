# Constraints

Last reviewed: 2026-09-16 (Batch 20, step 0).

Scope today is the floor and accessibility. Coverage, security, performance
and observability rows belong to Batch 20 priority #2 and are added then. This
file is not weakened in the same commit as a change that was failing it.

## Floor (always)

- No skipped, xfailed or deleted tests to get green. A test that is wrong is
  fixed in its own commit, with the reason in the message.
- No threshold below is lowered in the same change that was failing it.
- No new ruff exclusions or `# noqa` for test files.
- Tightening this file is quiet. Loosening it is a reviewed change of its own.
- Tests do not reach the network. The browser harness stands in for Telegram
  in every module that talks to it, and `tests/browser/test_harness.py` fails
  when a new one is missing. (Batch 20: saving an event waited on
  api.telegram.org, so a test was only as fast as the network.)
- A test about cost measures the work, not the machine: the CPU time of the
  thread that does it, fastest of a few runs, never the wall clock of a machine
  that is running Chromium and a server alongside. The bar itself stays.

- **Known failures.** A batch may carry failing tests on purpose, but only
  the ones in `tests/known_failures.txt`. On `fix/**` branches CI runs
  `scripts/check_known_failures.py`, which fails on any other failure and on a
  listed test that passes; each fix removes its lines. `main` and pull
  requests into it run the full suite, where the list has to be empty. This is
  not a way around the rule above: nothing is skipped or marked, the tests
  stay red and are counted as red.

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|-----------|------|------------|---------|
| Accessibility (external) | Zero serious or critical axe violations, tags `wcag2a wcag2aa wcag21a wcag21aa`, on list (light and dark), composer, detail and month | axe-core 4.13.0, vendored and SHA-256 pinned, in Chromium through Playwright: `pytest -m browser` | CI: every push to `main` and `fix/**`, and every PR |
| Accessibility (behaviour) | A11Y-01 to A11Y-09 in `docs/a11y/REQUIREMENTS.md` green | `pytest tests/browser tests/test_a11y_findings.py` | CI |
| Contrast (fast) | Fallback palette text pairs ≥ 4.5:1 | `pytest tests/test_a11y_findings.py` | Every local run (milliseconds) |
| Target size | Primary controls ≥ 44×44 CSS px (D3) | `test_primary_controls_are_at_least_44_css_pixels` | CI |
| Clean console | No page error and no CSP violation inside Telegram | teardown of the `open_app` fixture | CI |
| Lint | `ruff check .` clean | ruff 0.8.4 | CI |
| Production dependencies | No known vulnerabilities | `pip-audit --requirement requirements.txt` | CI |

The fast loop can leave the browser out with `pytest -m "not browser"`. CI
never does.

What the external check cannot see: axe-core's stacking model does not know
the browser's top layer, so inside an open modal `<dialog>` it calls every
line "overlapped" and measures nothing — a contrast test there passes without
looking. The `axe` fixture therefore shows open dialogs again non-modally
before each run (same markup, same pixels), and
`test_axe_can_still_measure_contrast_inside_an_open_sheet` fails if axe goes
blind again. Found in Batch 20 step 2b-2, when two axe states turned green
with no colour changed.

## Decisions

- **D1 — browser harness:** pytest, Playwright for Python, vendored
  axe-core. It stays in the project's language, uses a real browser, brings an
  outside WCAG opinion, and proves the CSP in the same run. The cost is one dev
  dependency and a Chromium download in CI.
- **D2 — order:** the Critical data-loss and dead-button findings come first
  (A11Y-02), in one commit.
- **D3 — target size:** 44×44 px for primary controls. That is WCAG 2.5.5
  (AAA) and the agent-skills checklist. The AA floor, 24×24 px (WCAG 2.2
  2.5.8), is already met everywhere measured. Delivered as an invisible touch
  area (an `::after` past the visible edge), so nothing grows; the test measures
  what a finger meets with `elementFromPoint`, not the painted box.
- **D4 — Telegram themes:** the app corrects a hint or subtitle colour that
  falls below 4.5:1 against the theme background, instead of passing it
  through. The user's theme is not the app's to choose, but readable text is.
  How, since Batch 20: a text colour from the theme that reads below
  4.5:1 keeps its hue and moves towards black, or white on a dark theme, only
  until it passes; it is never swapped for a fixed grey.

- **D5 — dialogs:** every modal is a native `<dialog>` opened through
  `static/modal.js`: one stack, so Escape and Telegram's back button close only
  the top dialog, Tab wraps inside it, and focus returns to its opener. It is
  migrated in three slices (2a, 2b, 2c), each proven on its own.
- **D6 — initial focus:** a dialog starts at its title; the confirm starts at
  Cancel. A screen reader announces where the user is, and no phone keyboard
  springs up before anything was chosen.

- **D7 — status messages:** the page's one status region (`#toast`,
  `role="status"`) follows the dialog on top, moved when a dialog opens or
  closes rather than when a message is written. Measured in Chromium: outside
  an open modal dialog the region is dropped from the accessibility tree.

- **D8 — event cards:** each card stays one button, named by its title and
  described by its own badges, dates and status. The alternative, a heading
  with a button stretched inside it, changed the keyboard focus ring (5,527
  pixels measured); this one changed nothing.
- **D9 — list status:** filters and searches announce how many events they
  leave, in a short status of its own; loading and reloading stay quiet.

- **D10 — validation errors:** shown under their field, visible, and only while
  the field is invalid; they replace the toast for these three checks, so a
  screen reader does not hear the same error twice.

- **D11 — colours (option 1):** a colour that fails keeps its hue and moves
  only as far as 4.6:1 on every background it sits on, measured by axe in the
  real screens, not only on paper. The brand has two roles: `--brand` is a
  fill with white text on it in every theme, `--brand-ink` is the brand as
  text, lighter in dark themes. Accent text (category and pin badges, the
  urgency badges, the detail page's buttons) follows the same rule as
  `--tone-*`, `--ink-*` and `--danger-ink` tokens with a dark set of their own,
  and is checked against Telegram's common dark surfaces too.
- **D12 — the year strip (Y1):** its day buttons stay as they look and click,
  but out of the Tab order; it sits inside `aria-hidden`, and the month grid
  is the keyboard way to the same day.

- **D13 — admin (Batch 20):** the only admin is `ADMIN_CHAT_ID` from the
  environment; no command grants the role. The admin's account is unlimited.
  Per-user limits (a number or unlimited) and the formula's numbers change at
  runtime from the bot and live in collections of their own, never on the user
  document the referral logic reads. Unlimited stops at `MAX_EVENTS_PER_USER`.
  Every change is audited. Card-render speed is pinned through its causes (the
  font and backdrop caches, no PNG optimize), with a 500 ms CPU safety net.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| — | none | | | | |
