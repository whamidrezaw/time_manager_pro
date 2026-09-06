from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import ping_database
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        await ping_database()
        return HealthResponse(
            status="ok",
            db="connected",
            ts=datetime.now(timezone.utc),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="db_down") from exc
