# Architecture decision records

One file per decision that a later change has to respect: its status, the
context that forced it, the decision, and what follows from it. A decision is
never edited away; a new record supersedes it (`Status: Superseded by 00NN`).
Add the next number with the same four sections; `tests/test_docs.py` checks
that every record has them and is listed here.

- [0001. Datetimes are timezone-aware from the database on](0001-timezone-aware-datetimes.md) (Batch 19)
- [0002. The app does not deny framing](0002-no-framing-denial.md) (Batch 19)
- [0003. Garbage collection waits a full period plus thirty days](0003-ttl-a-full-period-plus-thirty-days.md) (Batch 19)
- [0004. Each reminder occurrence has an idempotency key](0004-an-idempotency-key-per-reminder.md) (Batch 19)
- [0005. Reminders are triggered from outside, every minute](0005-reminders-are-triggered-from-outside.md) (Batch 18; measured in Batch 20)
- [0006. Every dialog is a native dialog on one modal stack](0006-native-dialogs-and-one-modal-stack.md) (Batch 20 (D5, D6, D7))
- [0007. A batch carries its failing tests by name](0007-the-known-failures-gate.md) (Batch 20)
- [0008. A failing colour keeps its hue and moves only as far as it must](0008-the-colour-contrast-rule.md) (Batch 20 (D4, D11))
- [0009. Limits are managed from the bot, by one admin](0009-admin-control-of-limits.md) (Batch 20 (D13))
- [0010. Tests reach no network and pin causes, not clocks](0010-tests-are-hermetic-and-measure-causes.md) (Batch 20)
- [0011. Logs are JSON lines, and RED comes from them](0011-structured-logs-and-red-from-log-lines.md) (Batch 23)
