from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

VALID_REPEAT_VALUES = ("none", "daily", "weekly", "monthly", "yearly")
VALID_CATEGORY_VALUES = (
    "general",
    "birthday",
    "work",
    "family",
    "health",
    "travel",
    "finance",
    "study",
    "other",
)


class APIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class InitDataPayload(APIModel):
    initData: str = Field(..., min_length=1)


class EventIdPayload(InitDataPayload):
    event_id: str = Field(..., min_length=1, max_length=64)


class SuccessResponse(APIModel):
    success: bool = True


class ErrorResponse(APIModel):
    success: bool = False
    error: str


class HealthResponse(APIModel):
    status: str
    db: str
    ts: datetime


class PaginationMeta(APIModel):
    has_more: bool
    returned: int
    skip: int


class MessageResponse(SuccessResponse):
    message: str


class GenericDataResponse(SuccessResponse):
    data: dict[str, Any]
