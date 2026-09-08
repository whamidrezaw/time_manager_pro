from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, validate_init_data
from app.services.referrals import (
    attach_referrer,
    parse_ref_payload,
    referral_overview,
)

router = APIRouter(prefix="/api", tags=["referral"])
logger = logging.getLogger("tm_pro.referral")


@router.post("/referral")
async def api_referral(request: Request, payload: InitDataPayload) -> dict:
    """The invite screen's only endpoint: state in, full picture out.

    This is also the second half of the attribution path. A tap on a share
    link opens the Mini App directly, and Telegram then hands the payload over
    as start_param inside initData — signed, so it is as trustworthy as the
    user id next to it. The /start branch in routes/telegram.py covers the
    other route in, for anyone who lands in the chat first.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)
    user_id = auth["user_id"]

    code = parse_ref_payload((auth.get("raw") or {}).get("start_param"))
    if code:
        try:
            await attach_referrer(user_id, code)
        except Exception:
            logger.exception("referral attach failed user_id=%s", user_id)

    return await referral_overview(user_id, settings)
