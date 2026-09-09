from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from telegram import Bot

from app.config import get_settings
from app.schemas.common import PaginationMeta
from app.schemas.requests import (
    AddEventRequest,
    DeleteEventRequest,
    EditEventRequest,
    ListEventsRequest,
    PinEventRequest,
    SaveChecklistRequest,
    SaveNoteRequest,
)
from app.schemas.responses import (
    EventMutationResponse,
    ListEventsResponse,
    NoteResponse,
    PinResponse,
)
from app.services.auth import READ_SCOPE, get_authenticated_user_id
from app.services.events import (
    add_event_for_user,
    delete_event_for_user,
    edit_event_for_user,
    list_events_for_user,
    save_checklist_for_user,
    save_note_for_user,
    set_pin_for_user,
)

router = APIRouter(prefix="/api", tags=["events"])
logger = logging.getLogger("tm_pro.events")


@router.post("/list", response_model=ListEventsResponse)
async def api_list(request: Request, payload: ListEventsRequest) -> ListEventsResponse:
    user_id = await get_authenticated_user_id(request, payload.initData, scope=READ_SCOPE)
    targets, has_more = await list_events_for_user(user_id, payload)

    return ListEventsResponse(
        success=True,
        targets=targets,
        has_more=has_more,
        meta=PaginationMeta(
            has_more=has_more,
            returned=len(targets),
            skip=payload.skip,
        ),
    )


@router.post("/add", response_model=EventMutationResponse)
async def api_add(request: Request, payload: AddEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await add_event_for_user(user_id, payload)

    settings = get_settings()
    try:
        async with Bot(token=settings.bot_token) as bot:
            await bot.send_message(
                chat_id=user_id,
                text=f'✅ Event "{payload.title}" was saved successfully.',
            )
    except Exception as exc:
        logger.warning("Confirmation message failed: user_id=%s error=%s", user_id, exc)

    return EventMutationResponse(success=True)


@router.post("/edit", response_model=EventMutationResponse)
async def api_edit(request: Request, payload: EditEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await edit_event_for_user(user_id, payload)
    return EventMutationResponse(success=True)


@router.post("/delete", response_model=EventMutationResponse)
async def api_delete(request: Request, payload: DeleteEventRequest) -> EventMutationResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    await delete_event_for_user(user_id, payload.event_id)
    return EventMutationResponse(success=True)


@router.post("/note", response_model=NoteResponse)
async def api_note(request: Request, payload: SaveNoteRequest) -> NoteResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    note = await save_note_for_user(user_id, payload)
    return NoteResponse(success=True, note=note)


@router.post("/checklist")
async def api_checklist(request: Request, payload: SaveChecklistRequest) -> dict:
    user_id = await get_authenticated_user_id(request, payload.initData)
    items = await save_checklist_for_user(user_id, payload)
    return {"success": True, "checklist": items}


@router.post("/pin", response_model=PinResponse)
async def api_pin(request: Request, payload: PinEventRequest) -> PinResponse:
    user_id = await get_authenticated_user_id(request, payload.initData)
    pinned = await set_pin_for_user(user_id, payload)
    return PinResponse(success=True, pinned=pinned)
