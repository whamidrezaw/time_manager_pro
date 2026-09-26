# 0008. A failing colour keeps its hue and moves only as far as it must

Status: Accepted
Decided: Batch 20 (D4, D11). Recorded 2026-09-26 (Batch 22).

## Context

Muted text read at 2.8 to 3.1:1, brand text at 4.24:1, urgency badges at 2.5
to 3.8:1 (axe had marked those incomplete), and no accent had a dark variant.
Telegram themes can also hand over colours that are too faint.

## Decision

Option 1: a colour that fails keeps its hue and moves only as far as 4.6:1 on
every background it sits on, tints composited, measured in the real screens.
The brand is a fill (`--brand`, white text on it) and an ink (`--brand-ink`,
lighter in dark themes). Accents are `--tone-*`, `--ink-*` and `--danger-ink`
tokens with a dark set, checked on Telegram's common dark surfaces too. A
Telegram text colour below 4.5:1 is moved the same way, never replaced.

## Consequences

The smallest visible change that passes. Oranges and yellows darken in light
themes. A static test reads the stylesheet, so a badge added later is checked
without being listed.
