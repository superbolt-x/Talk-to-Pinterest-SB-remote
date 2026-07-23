"""Pins — read tools, organic pin analytics, and create/delete executors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api.client import api_request, get_paginated

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

# Sensible default organic pin analytics metrics (override via metric_types).
DEFAULT_PIN_METRICS = [
    "IMPRESSION",
    "PIN_CLICK",
    "OUTBOUND_CLICK",
    "SAVE",
    "ENGAGEMENT",
]


# --- reads -----------------------------------------------------------------


def list_pins(config: PinloopConfig, *, max_items: int = 250) -> dict:
    """List pins owned by the authenticated account."""
    pins = get_paginated(config, "/pins", max_items=max_items)
    return {"pins": pins, "total_pins": len(pins)}


def get_pin(config: PinloopConfig, pin_id: str) -> dict:
    """Fetch a single pin by id."""
    return api_request(config, "GET", f"/pins/{pin_id}")


def get_pin_analytics(
    config: PinloopConfig,
    pin_id: str,
    *,
    start_date: str,
    end_date: str,
    metric_types: list[str] | None = None,
) -> dict:
    """Organic analytics for a single pin over an inclusive date range.

    Dates are "YYYY-MM-DD". metric_types defaults to a common set.
    """
    metrics = metric_types or DEFAULT_PIN_METRICS
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "metric_types": ",".join(metrics),
    }
    return api_request(config, "GET", f"/pins/{pin_id}/analytics", params=params)


# --- executors (called only from confirm_and_apply) ------------------------


def create_pin(
    config: PinloopConfig,
    *,
    board_id: str,
    title: str = "",
    description: str = "",
    link: str = "",
    image_url: str = "",
    board_section_id: str = "",
    alt_text: str = "",
) -> dict:
    """Create an image pin from a publicly reachable image URL."""
    body: dict = {
        "board_id": board_id,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    if title:
        body["title"] = title
    if description:
        body["description"] = description
    if link:
        body["link"] = link
    if board_section_id:
        body["board_section_id"] = board_section_id
    if alt_text:
        body["alt_text"] = alt_text
    return api_request(config, "POST", "/pins", json_body=body)


def delete_pin(config: PinloopConfig, *, pin_id: str) -> dict:
    api_request(config, "DELETE", f"/pins/{pin_id}")
    return {"deleted_pin_id": pin_id}
