"""User account + ad account discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api.client import api_request, get_paginated

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig


def require_account(config: PinloopConfig, account_id: str) -> str:
    """Return the ad account id to use, or raise if none available.

    Falls back to PINTEREST_AD_ACCOUNT_ID. No account is assumed silently —
    callers must pass one or configure a default.
    """
    resolved = (account_id or config.ads.ad_account_id or "").strip()
    if not resolved:
        raise RuntimeError(
            "No ad account specified. Pass account_id, or set a default with "
            "PINTEREST_AD_ACCOUNT_ID. Call list_ad_accounts to see options."
        )
    return resolved


def get_user_account(config: PinloopConfig) -> dict:
    """The authenticated Pinterest user/business account (also a token probe)."""
    return api_request(config, "GET", "/user_account")


def list_ad_accounts(config: PinloopConfig) -> dict:
    """Every ad account the authenticated user can access."""
    accounts = get_paginated(config, "/ad_accounts", max_items=500)
    return {
        "ad_accounts": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "country": a.get("country"),
                "currency": a.get("currency"),
                "owner": (a.get("owner") or {}).get("username"),
            }
            for a in accounts
        ],
        "total_ad_accounts": len(accounts),
        "default_ad_account_id": config.ads.ad_account_id or "(unset)",
    }
