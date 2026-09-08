from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.config import get_settings
from app.db import get_events_collection
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, validate_init_data
from app.utils.occurrences import expand_occurrences

router = APIRouter(tags=["calendar"])
logger = logging.getLogger("tm_pro.calendar")

# A year of the pixel grid is 371 days; anything past that is not a view the
# Mini App has, so it is a malformed request rather than a big one.
MAX_RANGE_DAYS = 400

# The month view wants the events themselves so a tapped day can list them
# without a second round trip. The year grid only ever draws counts, and
# shipping a year of titles to colour 371 squares would be pure waste.
ITEMS_RANGE_DAYS = 62
MAX_ITEMS = 400

PROJECTION = {
    "title": 1, "date_iso": 1, "tz_name": 1, "all_day": 1, "time_hm": 1,
    "repeat": 1, "repeat_until": 1, "category": 1, "pinned": 1,
}


class CalendarPayload(InitDataPayload):
    start: str = Field(min_length=10, max_length=10)
    end: str = Field(min_length=10, max_length=10)


@router.post("/api/calendar")
async def api_calendar(request: Request, payload: CalendarPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    try:
        start = date.fromisoformat(payload.start)
        end = date.fromisoformat(payload.end)
    except ValueError:
        raise HTTPException(status_code=400, detail="INVALID_DATE_FORMAT") from None

    if end < start:
        raise HTTPException(status_code=400, detail="INVALID_RANGE")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="RANGE_TOO_LARGE")

    want_items = (end - start).days <= ITEMS_RANGE_DAYS
    days: dict[str, int] = {}
    items: list[dict] = []

    cursor = get_events_collection().find({"user_id": auth["user_id"]}, PROJECTION)
    async for doc in cursor:
        for when in expand_occurrences(doc, start, end):
            key = when.isoformat()
            days[key] = days.get(key, 0) + 1

            if want_items and len(items) < MAX_ITEMS:
                items.append({
                    "id": str(doc["_id"]),
                    "date": key,
                    "title": doc.get("title", ""),
                    "category": doc.get("category", "general"),
                    "pinned": bool(doc.get("pinned")),
                    "all_day": bool(doc.get("all_day", True)),
                    "time_hm": doc.get("time_hm"),
                })

    items.sort(key=lambda item: (item["date"], item["title"]))
    return {"success": True, "days": days, "items": items,
            "start": payload.start, "end": payload.end}
