from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import Field

from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.chats import destinations_for, link_member, parse_chat_payload

router = APIRouter(tags=["chats"])
logger = logging.getLogger("tm_pro.chats_api")


class LinkPayload(InitDataPayload):
    token: str = Field(min_length=1, max_length=80)


@router.post("/api/chats")
async def api_chats(request: Request, payload: InitDataPayload) -> dict:
    """The destinations this user may send to, for the picker in the form."""
    auth = await validate_init_data(request, payload.initData, scope=READ_SCOPE)
    return {"success": True, "chats": await destinations_for(auth["user_id"])}


@router.post("/api/chats/link")
async def api_chats_link(request: Request, payload: LinkPayload) -> dict:
    """Someone tapped the link the bot posted. Membership decides, not the link."""
    auth = await validate_init_data(request, payload.initData, scope=WRITE_SCOPE)

    token = parse_chat_payload(payload.token) or payload.token
    return await link_member(auth["user_id"], token)
