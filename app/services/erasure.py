"""Erasure on request: /deletemydata (Batch 27, step 3; ADR 0017; GDPR Art. 17).

Everything the app keeps about one user goes: their events, with the public and
join links on them; their user and referral record; their username; a limit
the admin set for them; their rate-limit counters; and their membership of
group chats. Invitees stop pointing at them, and the admin's audit trail keeps
what was done but no longer to whom. What stays belongs to others: a group
chat's record, other members' own copies of a shared event.
"""
from __future__ import annotations

import logging
import re

from app.db import get_database

logger = logging.getLogger("tm_pro.erasure")

# Every collection the code uses is in one of these two lists; a test fails
# when a new one appears in neither, so it cannot be forgotten here.
ERASED = ("events", "users", "usernames", "limit_overrides", "rate_limits", "chats", "admin_audit")
WITHOUT_USER_DATA = ("settings", "alert_state")
DELETED = "deleted user"


async def delete_user_data(user_id: str) -> dict[str, int]:
    """Erase one user's data and return how many documents each step touched."""
    uid = str(user_id)
    db = get_database()
    names = [doc["_id"] async for doc in db["usernames"].find({"user_id": uid}, {"_id": 1})]
    whom: list[dict] = [{"target": uid}]
    if names:  # the admin may have typed @Hamid for the stored hamid
        pattern = "^@?(" + "|".join(re.escape(str(name)) for name in names) + ")$"
        whom.append({"target": {"$regex": pattern, "$options": "i"}})
    counts = {
        "events": (await db["events"].delete_many({"user_id": uid})).deleted_count,
        "users": (await db["users"].delete_many({"_id": uid})).deleted_count,
        "invitees": (await db["users"].update_many(
            {"referred_by": uid}, {"$unset": {"referred_by": ""}})).modified_count,
        "usernames": (await db["usernames"].delete_many({"user_id": uid})).deleted_count,
        "limit_overrides": (await db["limit_overrides"].delete_many({"_id": uid})).deleted_count,
        "rate_limits": (await db["rate_limits"].delete_many({"user_id": uid})).deleted_count,
        "chats": (await db["chats"].update_many(
            {"members": uid}, {"$pull": {"members": uid}})).modified_count,
        "admin_audit": (await db["admin_audit"].update_many(
            {"$or": whom}, {"$set": {"target": DELETED}})).modified_count,
    }
    # No id in this line: forgetting who it was is the point.
    logger.info("user data erased on request", extra={"event": "user_data_erased", **counts})
    return counts
