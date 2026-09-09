from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.common import ChecklistItem
from app.schemas.requests import SaveChecklistRequest
from app.services.share_group import SHARED_FIELDS


def _request(items) -> SaveChecklistRequest:
    return SaveChecklistRequest(
        initData="x", event_id="507f1f77bcf86cd799439011", checklist=items
    )


def test_an_item_needs_text() -> None:
    with pytest.raises(ValidationError):
        ChecklistItem(text="")


def test_an_item_defaults_to_unticked() -> None:
    assert ChecklistItem(text="Buy the cake").done is False


def test_text_is_capped() -> None:
    with pytest.raises(ValidationError):
        ChecklistItem(text="x" * 201)


def test_a_checklist_is_capped_at_fifty_lines() -> None:
    """One document field, not a todo app."""
    fifty = [{"text": f"item {n}"} for n in range(50)]

    assert len(_request(fifty).checklist) == 50
    with pytest.raises(ValidationError):
        _request(fifty + [{"text": "one too many"}])


def test_an_empty_checklist_is_valid() -> None:
    """Clearing the last item has to be expressible."""
    assert _request([]).checklist == []


def test_the_checklist_is_personal_on_a_shared_event() -> None:
    """The creator owns what the event is; the list of what to buy for it is
    the same kind of private as the note."""
    assert "checklist" not in SHARED_FIELDS
    assert "note" not in SHARED_FIELDS
