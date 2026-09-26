# Definition of Done

A change is done when every line below holds. This is the project-wide bar,
adapted from the agent-skills `definition-of-done` reference to this
repository; a task's own acceptance criteria come on top of it.

## Correctness
- [ ] What it adds or fixes is pinned by a test that failed first. A guard that
      passes from the start is proved by a mutation that makes it fail.
- [ ] `python scripts/check_known_failures.py` passes: exactly the tests in
      `tests/known_failures.txt` fail, and on `main` that list is empty.
- [ ] CI is green on the branch and on the pull request into `main`, where the
      full suite runs with the coverage floor.

## Quality
- [ ] `ruff check .` is clean, with no new `# noqa`.
- [ ] No dead code, stale comment or unused string is left behind: removed, not
      hidden.
- [ ] Every limit in `CONSTRAINTS.md` holds; loosening one is a reviewed change
      of its own.

## Accessibility and UI
- [ ] axe finds nothing serious or critical on the screens it touches, light
      and dark.
- [ ] A visible change was chosen from options shown as screenshots, and the
      screens it must not change are pixel-compared against a baseline taken
      twice.

## Security
- [ ] No secret in code, logs or chat; input is validated where it enters; new
      authority, like the admin's, comes only from the environment.
- [ ] `pip-audit` is clean for what ships.

## Documentation
- [ ] A decision later changes must respect has an ADR in `docs/adr`, new or
      superseding an old one.
- [ ] README, `docs/DEBUGGING.md` and `docs/a11y/REQUIREMENTS.md` describe what
      the code does now.

## Ship-readiness
- [ ] It arrives as one checksum-guarded apply script, idempotent on a second
      run, proved on a fresh clone.
- [ ] A risky change has a way back: revert the merge commit, and redeploy the
      previous deploy on Render.
- [ ] A new critical path has logs, a metric and, where users would notice it
      failing, an alert (from Stage 3 on).
