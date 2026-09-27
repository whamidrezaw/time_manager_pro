from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId


def safe_object_id(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except Exception as exc:
        raise ValueError("invalid object id") from exc


def object_id_str(value: ObjectId) -> str:
    return str(value)


def is_valid_object_id(raw: str) -> bool:
    try:
        ObjectId(raw)
        return True
    except (InvalidId, TypeError):  # not an id, not a bug
        return False
