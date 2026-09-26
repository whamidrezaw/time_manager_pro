# 0017. Erasure on request, by a bot command with a confirmation

Status: Accepted
Decided: Batch 27, from the Stage 5 audit (P1), chosen from options. Recorded 2026-09-26 (Batch 27).

## Context
Events could be deleted one at a time, but the user record, the username the
admin feature keeps, referral records and chat links had no way out. Under
the GDPR a user has a right to erasure, and the service is run from Germany.

## Decision
`/deletemydata`, in a private chat, answers with a warning and two buttons.
The confirming button carries the sender's id and works only for that sender
and only for ten minutes. `delete_user_data` then erases every collection that
holds the user's data and anonymises the admin's audit trail; the collections
it knows are listed, and a test fails when the code uses one it does not.

## Consequences
Erasure is complete within the app and leaves other users' data as it was, a
group chat keeping its record without this member. Messages already sent in
Telegram and Render's logs are outside its reach; logs expire with Render's
retention. The user starts fresh if they open the app again.
