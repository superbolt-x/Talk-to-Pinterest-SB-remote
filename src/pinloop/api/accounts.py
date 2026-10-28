"""User account + ad account discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api.client import api_request, discover_ad_accounts
from pinloop.auth import profile_names

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
    """The authenticated Pinterest user/business account (also a token probe).

    One entry per configured profile (login); a failing profile is reported
    in place rather than hiding the others.
    """
    names = profile_names(config)
    if len(names) < 2:
        return api_request(config, "GET", "/user_account")
    profiles: dict[str, dict] = {}
    for name in names:
        try:
            profiles[name] = api_request(config, "GET", "/user_account", profile=name)
        except Exception as exc:  # noqa: BLE001
            profiles[name] = {"error": str(exc)}
    return {"profiles": profiles}


def list_ad_accounts(config: PinloopConfig) -> dict:
    """Every ad account any configured login can access, tagged with its profile."""
    accounts, errors = discover_ad_accounts(config)
    result = {
        "ad_accounts": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "country": a.get("country"),
                "currency": a.get("currency"),
                "owner": (a.get("owner") or {}).get("username"),
                "profile": a["profile"],
            }
            for a in accounts
        ],
        "total_ad_accounts": len(accounts),
        "default_ad_account_id": config.ads.ad_account_id or "(unset)",
    }
    if errors:
        result["profile_errors"] = errors
    return result
