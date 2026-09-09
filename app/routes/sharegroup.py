from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.share_group import (
    find_by_token,
    invite_url,
    join_group,
    member_count,
    parse_share_payload,
    start_group,
)
from app.services.telegram_api import get_first_name
from app.utils.ids import safe_object_id

router = APIRouter(tags=["shared-events"])
logger = logging.getLogger("tm_pro.sharegroup")


class GroupPayload(InitDataPayload):
    event_id: str = Field(min_length=1, max_length=64)


class TokenPayload(InitDataPayload):
    token: str = Field(min_length=1, max_length=80)


class JoinPayload(TokenPayload):
    timezone: str = Field(default="UTC", min_length=1, max_length=128)


@router.post("/api/group/link")
async def api_group_link(request: Request, payload: GroupPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    return await start_group(auth["user_id"], oid, settings)


@router.post("/api/group/invite")
async def api_group_invite(request: Request, payload: TokenPayload) -> dict:
    """What the invited person sees before deciding.

    The name is fetched live and returned for this one screen; nothing about
    the inviter is written to the joiner's account.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    token = parse_share_payload(payload.token) or payload.token
    origin = await find_by_token(token)
    if not origin:
        raise HTTPException(status_code=404, detail="INVITE_NOT_FOUND")

    owner_id = str(origin.get("user_id"))
    return {
        "success": True,
        "title": origin.get("title", ""),
        "date_iso": origin.get("date_iso", ""),
        "date_jalali": origin.get("date_jalali", ""),
        "category": origin.get("category", "general"),
        "from_name": await get_first_name(owner_id, settings),
        "is_own": owner_id == str(auth["user_id"]),
        "members": await member_count(origin.get("share_id")),
    }


@router.post("/api/group/join")
async def api_group_join(request: Request, payload: JoinPayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    token = parse_share_payload(payload.token) or payload.token
    return await join_group(auth["user_id"], token, payload.timezone, settings)


@router.post("/api/group/state")
async def api_group_state(request: Request, payload: GroupPayload) -> dict:
    """Only for the invite button: does this event already have a link?"""
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)

    try:
        oid = safe_object_id(payload.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="INVALID_ID_FORMAT") from exc

    from app.db import get_events_collection

    event = await get_events_collection().find_one(
        {"_id": oid, "user_id": auth["user_id"]},
        {"share_id": 1, "share_role": 1, "share_token": 1},
    )
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND_OR_UNAUTHORIZED")

    token = event.get("share_token")
    return {
        "success": True,
        "role": event.get("share_role"),
        "invite_url": invite_url(token, settings) if token else None,
        "members": await member_count(event.get("share_id")),
    }
