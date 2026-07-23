"""Boards — read tools and create/delete executors.

Organic Pinterest surface: a board is a collection of pins owned by the
authenticated account. Reads are safe; create/delete run only via the
confirm_and_apply safety layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api.client import api_request, get_paginated

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

_VALID_PRIVACY = {"PUBLIC", "PROTECTED", "SECRET"}


# --- reads -----------------------------------------------------------------


def list_boards(config: PinloopConfig, *, privacy: str = "", max_items: int = 250) -> dict:
    """List boards owned by the authenticated account."""
    params: dict = {}
    if privacy:
        params["privacy"] = privacy.upper()
    boards = get_paginated(config, "/boards", params=params, max_items=max_items)
    return {"boards": boards, "total_boards": len(boards)}


def get_board(config: PinloopConfig, board_id: str) -> dict:
    """Fetch a single board by id."""
    return api_request(config, "GET", f"/boards/{board_id}")


def list_board_sections(config: PinloopConfig, board_id: str, *, max_items: int = 250) -> dict:
    """List the sections within a board."""
    sections = get_paginated(
        config, f"/boards/{board_id}/sections", max_items=max_items
    )
    return {"board_id": board_id, "sections": sections, "total_sections": len(sections)}


def list_board_pins(config: PinloopConfig, board_id: str, *, max_items: int = 250) -> dict:
    """List the pins on a board."""
    pins = get_paginated(config, f"/boards/{board_id}/pins", max_items=max_items)
    return {"board_id": board_id, "pins": pins, "total_pins": len(pins)}


# --- executors (called only from confirm_and_apply) ------------------------


def create_board(
    config: PinloopConfig,
    *,
    name: str,
    description: str = "",
    privacy: str = "PUBLIC",
) -> dict:
    body = {"name": name, "privacy": privacy.upper()}
    if description:
        body["description"] = description
    return api_request(config, "POST", "/boards", json_body=body)


def delete_board(config: PinloopConfig, *, board_id: str) -> dict:
    api_request(config, "DELETE", f"/boards/{board_id}")
    return {"deleted_board_id": board_id}


def validate_privacy(privacy: str) -> list[str]:
    if privacy.upper() not in _VALID_PRIVACY:
        return [f"privacy must be one of {sorted(_VALID_PRIVACY)}, got '{privacy}'"]
    return []
