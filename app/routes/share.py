from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import Field

from app.config import get_settings
from app.schemas.common import InitDataPayload
from app.services.auth import READ_SCOPE, WRITE_SCOPE, validate_init_data
from app.services.cards import FontsMissing, fonts_available, render_event_card
from app.services.reminders import event_language
from app.services.sharing import (
    card_url,
    get_public_event,
    get_share_state,
    miniapp_url,
    public_url,
    set_share_state,
)
from app.services.telegram_api import build_photo_result, save_prepared_inline_message
from app.utils.i18n import t

router = APIRouter(tags=["sharing"])
logger = logging.getLogger("tm_pro.share")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))

# The countdown moves once a day, so a long cache would serve a stale number
# and a zero-length one would re-render on every scroll past a preview.
CARD_CACHE = "public, max-age=900"


class SharePayload(InitDataPayload):
    event_id: str = Field(min_length=1, max_length=64)


class ShareTogglePayload(SharePayload):
    enabled: bool


@router.post("/api/share/state")
async def api_share_state(request: Request, payload: SharePayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=READ_SCOPE)
    return await get_share_state(auth["user_id"], payload.event_id, settings)


@router.post("/api/share/toggle")
async def api_share_toggle(request: Request, payload: ShareTogglePayload) -> dict:
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)
    return await set_share_state(
        auth["user_id"], payload.event_id, payload.enabled, settings
    )


@router.post("/api/share/prepare")
async def api_share_prepare(request: Request, payload: SharePayload) -> dict:
    """Hand the Mini App a prepared message id for tg.shareMessage.

    Sharing the picture needs the picture to be reachable by Telegram's own
    servers, so this refuses politely when the event is not public yet rather
    than turning sharing on behind the owner's back.
    """
    settings = get_settings()
    auth = await validate_init_data(request, payload.initData, settings, scope=WRITE_SCOPE)

    state = await get_share_state(auth["user_id"], payload.event_id, settings)
    if not state["enabled"]:
        raise HTTPException(status_code=400, detail="SHARING_DISABLED")
    if not fonts_available():
        raise HTTPException(status_code=503, detail="CARD_UNAVAILABLE")

    token = state["token"]
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    language = event_language(event)
    result = build_photo_result(
        card=card_url(token, settings),
        link=miniapp_url(token, settings),
        title=str(event.get("title", ""))[:120],
        caption=t("share_caption", language).format(
            title=str(event.get("title", ""))[:120]
        ),
        button=t("share_button", language),
    )

    try:
        prepared = await save_prepared_inline_message(auth["user_id"], result, settings)
    except Exception:
        # The Mini App falls back to a plain link share, so this is a normal
        # outcome on an older client rather than a server error.
        raise HTTPException(status_code=502, detail="PREPARE_FAILED") from None

    return {"success": True, "prepared_message_id": prepared}


@router.get("/c/{token}/card.png")
async def public_card(token: str) -> Response:
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    settings = get_settings()
    try:
        png = render_event_card(
            {**event, "bot_handle": f"@{settings.telegram_bot_username}"},
            event_language(event),
        )
    except FontsMissing:
        logger.error("static/fonts is empty — the share card cannot be rendered")
        raise HTTPException(status_code=503, detail="CARD_UNAVAILABLE") from None

    return Response(content=png, media_type="image/png", headers={"Cache-Control": CARD_CACHE})


@router.get("/c/{token}")
async def public_countdown(request: Request, token: str):
    """The public face of an event: one URL that behaves in both worlds.

    Inside Telegram the page bounces straight to the Mini App; in an ordinary
    browser it renders the card with a way in. The bounce is a best-effort
    user-agent check, so the button below it is always present and always
    works — nobody ends up staring at a page that did nothing.
    """
    event = await get_public_event(token)
    if not event:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    settings = get_settings()
    language = event_language(event)

    return templates.TemplateResponse(
        request,
        "countdown.html",
        {
            "lang": language,
            "direction": "rtl" if language == "fa" else "ltr",
            "title": str(event.get("title", "")),
            "card_url": card_url(token, settings),
            "page_url": public_url(token, settings),
            "miniapp_url": miniapp_url(token, settings),
            "bot_handle": f"@{settings.telegram_bot_username}",
            "open_label": t("share_open_button", language),
            "made_with": t("share_made_with", language),
        },
    )
