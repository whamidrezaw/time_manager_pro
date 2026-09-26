# 0007. A batch carries its failing tests by name

Status: Accepted
Decided: Batch 20. Recorded 2026-09-26 (Batch 22).

## Context

A batch keeps its findings as failing tests until they are fixed. CI was then
red on every push and said nothing: a regression, or a flaky test, looked like
the known findings.

## Decision

`tests/known_failures.txt` names the tests that fail on purpose, and
`scripts/check_known_failures.py` passes only if exactly those fail. CI runs it
on `fix/**` and `feat/**` branches and runs the full suite on `main` and pull
requests, where the list must be empty. The same script is the gate before
every commit. Nothing is skipped or marked `xfail`.

## Consequences

A red run always means something new, and says what, by name. Each fix removes
its lines; Batch 20 ended with the list empty. Two flaky tests were found this
way instead of being retried until green (ADR 0010).
