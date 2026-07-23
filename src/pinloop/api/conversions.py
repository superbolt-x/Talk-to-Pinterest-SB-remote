"""Conversions — conversion tags (read/create) and the Conversions API (events).

Two distinct things live here:
  * Conversion tags: the pixel-like tag definitions on an ad account.
  * Conversion events: server-side events sent via Pinterest's Conversions API
    (CAPI), POST /ad_accounts/{id}/events. PII in user_data is SHA-256 hashed
    before it leaves this process, per Pinterest's requirements.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pinloop.api.client import api_request

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

VALID_ACTION_SOURCES = {"app_android", "app_ios", "web", "offline"}

# user_data fields Pinterest expects as arrays of SHA-256 hashes.
_HASH_FIELDS = {"em", "ph", "fn", "ln", "ge", "db", "ct", "st", "zp", "country", "external_id"}


# --- conversion tags -------------------------------------------------------


def list_conversion_tags(config: PinloopConfig, *, account_id: str) -> dict:
    """List conversion tags configured on an ad account."""
    data = api_request(config, "GET", f"/ad_accounts/{account_id}/conversion_tags")
    items = data.get("items", data) if isinstance(data, dict) else data
    return {"ad_account_id": account_id, "conversion_tags": items}


def get_conversion_tag(
    config: PinloopConfig, *, account_id: str, conversion_tag_id: str
) -> dict:
    """Fetch a single conversion tag by id."""
    return api_request(
        config, "GET", f"/ad_accounts/{account_id}/conversion_tags/{conversion_tag_id}"
    )


def create_conversion_tag(
    config: PinloopConfig,
    *,
    account_id: str,
    name: str,
    aem_enabled: bool | None = None,
) -> dict:
    """Create a conversion tag on an ad account (executor)."""
    body: dict = {"name": name}
    if aem_enabled is not None:
        body["aem_enabled"] = aem_enabled
    return api_request(
        config, "POST", f"/ad_accounts/{account_id}/conversion_tags", json_body=body
    )


# --- conversion events (Conversions API) -----------------------------------


def _is_hashed(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value.lower())
    )


def _hash_scalar(field: str, value: Any) -> str:
    s = str(value).strip()
    if field == "ph":
        s = "".join(ch for ch in s if ch.isdigit())
    else:
        s = s.lower()
    return hashlib.sha256(s.encode()).hexdigest()


def normalize_user_data(user_data: dict | None) -> dict:
    """Hash PII fields (email/phone/name/...) to SHA-256, wrapping them as arrays.

    Already-hashed values (64-char hex) are passed through untouched.
    Non-PII fields (client_ip_address, client_user_agent, click_id) are left
    as-is.
    """
    out: dict = {}
    for key, value in (user_data or {}).items():
        if key in _HASH_FIELDS:
            values = value if isinstance(value, list) else [value]
            out[key] = [v if _is_hashed(v) else _hash_scalar(key, v) for v in values]
        else:
            out[key] = value
    return out


def send_conversion_event(
    config: PinloopConfig,
    *,
    account_id: str,
    event_name: str,
    action_source: str,
    event_time: int,
    user_data: dict | None = None,
    custom_data: dict | None = None,
    event_id: str = "",
    test: bool = False,
) -> dict:
    """Send one server-side conversion event via the Conversions API (executor).

    event_time is a UNIX timestamp (seconds). Set test=True to route the event
    to Pinterest's test pipeline (does not affect reporting).
    """
    event: dict = {
        "event_name": event_name,
        "action_source": action_source,
        "event_time": int(event_time),
    }
    if event_id:
        event["event_id"] = event_id
    if user_data:
        event["user_data"] = normalize_user_data(user_data)
    if custom_data:
        event["custom_data"] = custom_data

    params = {"test": "true"} if test else None
    return api_request(
        config,
        "POST",
        f"/ad_accounts/{account_id}/events",
        params=params,
        json_body={"data": [event]},
    )
