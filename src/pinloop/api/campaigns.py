"""Campaign management — campaigns, ad groups, ads: reads, analytics, executors.

Hierarchy: ad account > campaign > ad group > ad (an ad promotes a pin).

Reads are safe. create/update executors are invoked only from the
confirm_and_apply safety layer. Budgets/bids are accepted in account currency
(e.g. dollars) at the tool boundary and converted to Pinterest micro-currency
here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api.client import api_request, from_micro, get_paginated, to_micro

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

# Conservative, widely-valid default analytics columns (override per call).
DEFAULT_ANALYTICS_COLUMNS = [
    "SPEND_IN_DOLLAR",
    "IMPRESSION_1",
    "CLICKTHROUGH_1",
    "OUTBOUND_CLICK_1",
    "TOTAL_CONVERSIONS",
    "CTR",
    "CPC_IN_DOLLAR",
    "ECPM_IN_DOLLAR",
]

VALID_GRANULARITY = {"TOTAL", "DAY", "HOUR", "WEEK", "MONTH"}
VALID_OBJECTIVES = {
    "AWARENESS",
    "CONSIDERATION",
    "VIDEO_VIEW",
    "WEB_CONVERSION",
    "CATALOG_SALES",
    "WEB_SESSIONS",
}
VALID_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED"}


# --- reads -----------------------------------------------------------------


def list_campaigns(
    config: PinloopConfig,
    *,
    account_id: str,
    entity_statuses: list[str] | None = None,
    max_items: int = 250,
) -> dict:
    """List campaigns in an ad account, optionally filtered by status."""
    params: dict = {}
    if entity_statuses:
        params["entity_statuses"] = ",".join(s.upper() for s in entity_statuses)
    campaigns = get_paginated(
        config, f"/ad_accounts/{account_id}/campaigns", params=params, max_items=max_items
    )
    return {
        "ad_account_id": account_id,
        "campaigns": [_annotate_budget(c) for c in campaigns],
        "total_campaigns": len(campaigns),
    }


def get_campaign(config: PinloopConfig, *, account_id: str, campaign_id: str) -> dict:
    """Fetch a single campaign by id."""
    return _annotate_budget(
        api_request(config, "GET", f"/ad_accounts/{account_id}/campaigns/{campaign_id}")
    )


def list_ad_groups(
    config: PinloopConfig,
    *,
    account_id: str,
    campaign_ids: list[str] | None = None,
    max_items: int = 250,
) -> dict:
    """List ad groups in an ad account, optionally filtered by campaign."""
    params: dict = {}
    if campaign_ids:
        params["campaign_ids"] = ",".join(campaign_ids)
    ad_groups = get_paginated(
        config, f"/ad_accounts/{account_id}/ad_groups", params=params, max_items=max_items
    )
    return {
        "ad_account_id": account_id,
        "ad_groups": [_annotate_budget(g) for g in ad_groups],
        "total_ad_groups": len(ad_groups),
    }


def list_ads(
    config: PinloopConfig,
    *,
    account_id: str,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    max_items: int = 250,
) -> dict:
    """List ads in an ad account, optionally filtered by campaign or ad group."""
    params: dict = {}
    if campaign_ids:
        params["campaign_ids"] = ",".join(campaign_ids)
    if ad_group_ids:
        params["ad_group_ids"] = ",".join(ad_group_ids)
    ads = get_paginated(
        config, f"/ad_accounts/{account_id}/ads", params=params, max_items=max_items
    )
    return {"ad_account_id": account_id, "ads": ads, "total_ads": len(ads)}


def _analytics(
    config: PinloopConfig,
    *,
    account_id: str,
    entity: str,
    id_param: str,
    ids: list[str],
    start_date: str,
    end_date: str,
    columns: list[str] | None,
    granularity: str,
) -> dict:
    if not ids:
        raise RuntimeError(f"{id_param} is required for {entity} analytics")
    if granularity.upper() not in VALID_GRANULARITY:
        raise RuntimeError(
            f"granularity must be one of {sorted(VALID_GRANULARITY)}, got '{granularity}'"
        )
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "columns": ",".join(columns or DEFAULT_ANALYTICS_COLUMNS),
        "granularity": granularity.upper(),
        id_param: ",".join(ids),
    }
    data = api_request(
        config, "GET", f"/ad_accounts/{account_id}/{entity}/analytics", params=params
    )
    return {
        "ad_account_id": account_id,
        "entity": entity,
        "start_date": start_date,
        "end_date": end_date,
        "granularity": granularity.upper(),
        "rows": data if isinstance(data, list) else data.get("items", data),
    }


def get_campaign_analytics(
    config: PinloopConfig,
    *,
    account_id: str,
    campaign_ids: list[str],
    start_date: str,
    end_date: str,
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Campaign-level performance for an inclusive date range."""
    return _analytics(
        config, account_id=account_id, entity="campaigns", id_param="campaign_ids",
        ids=campaign_ids, start_date=start_date, end_date=end_date,
        columns=columns, granularity=granularity,
    )


def get_ad_group_analytics(
    config: PinloopConfig,
    *,
    account_id: str,
    ad_group_ids: list[str],
    start_date: str,
    end_date: str,
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Ad-group-level performance for an inclusive date range."""
    return _analytics(
        config, account_id=account_id, entity="ad_groups", id_param="ad_group_ids",
        ids=ad_group_ids, start_date=start_date, end_date=end_date,
        columns=columns, granularity=granularity,
    )


def get_ad_analytics(
    config: PinloopConfig,
    *,
    account_id: str,
    ad_ids: list[str],
    start_date: str,
    end_date: str,
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Ad-level performance for an inclusive date range."""
    return _analytics(
        config, account_id=account_id, entity="ads", id_param="ad_ids",
        ids=ad_ids, start_date=start_date, end_date=end_date,
        columns=columns, granularity=granularity,
    )


# --- executors (called only from confirm_and_apply) ------------------------


def _unwrap_bulk(response: dict, operation: str) -> dict:
    """Pinterest bulk create/update returns {"items": [{"data", "exceptions"}]}.

    Surface any per-item exceptions as an error rather than silently returning
    a half-created entity.
    """
    items = response.get("items") if isinstance(response, dict) else None
    if not items:
        return response
    problems = [it.get("exceptions") for it in items if it.get("exceptions")]
    if problems:
        raise RuntimeError(f"{operation} returned per-item errors: {problems}")
    data = [it.get("data") for it in items]
    return {"created": data[0] if len(data) == 1 else data}


def create_campaign(
    config: PinloopConfig,
    *,
    account_id: str,
    name: str,
    objective_type: str,
    status: str = "PAUSED",
    daily_budget: float | None = None,
    lifetime_budget: float | None = None,
) -> dict:
    """Create a campaign (defaults to PAUSED). Budgets are in account currency."""
    item: dict = {
        "name": name,
        "objective_type": objective_type.upper(),
        "status": status.upper(),
    }
    if daily_budget is not None:
        item["daily_spend_cap"] = to_micro(daily_budget)
    if lifetime_budget is not None:
        item["lifetime_spend_cap"] = to_micro(lifetime_budget)
    resp = api_request(
        config, "POST", f"/ad_accounts/{account_id}/campaigns", json_body=[item]
    )
    return _unwrap_bulk(resp, "create_campaign")


def create_ad_group(
    config: PinloopConfig,
    *,
    account_id: str,
    campaign_id: str,
    name: str,
    status: str = "PAUSED",
    budget: float | None = None,
    bid: float | None = None,
    billable_event: str = "IMPRESSION",
) -> dict:
    """Create an ad group under a campaign (defaults to PAUSED)."""
    item: dict = {
        "campaign_id": campaign_id,
        "name": name,
        "status": status.upper(),
        "billable_event": billable_event.upper(),
    }
    if budget is not None:
        item["budget_in_micro_currency"] = to_micro(budget)
    if bid is not None:
        item["bid_in_micro_currency"] = to_micro(bid)
    resp = api_request(
        config, "POST", f"/ad_accounts/{account_id}/ad_groups", json_body=[item]
    )
    return _unwrap_bulk(resp, "create_ad_group")


_ENTITY_PATH = {"campaign": "campaigns", "ad_group": "ad_groups", "ad": "ads"}


def update_entity_status(
    config: PinloopConfig,
    *,
    account_id: str,
    entity_type: str,
    entity_id: str,
    status: str,
) -> dict:
    """PATCH a campaign/ad_group/ad to ACTIVE, PAUSED, or ARCHIVED."""
    path_seg = _ENTITY_PATH[entity_type]
    resp = api_request(
        config,
        "PATCH",
        f"/ad_accounts/{account_id}/{path_seg}",
        json_body=[{"id": entity_id, "status": status.upper()}],
    )
    return _unwrap_bulk(resp, f"update_{entity_type}_status")


# --- helpers ---------------------------------------------------------------


def _annotate_budget(entity: dict) -> dict:
    """Add human-readable currency fields alongside Pinterest's micro amounts."""
    if not isinstance(entity, dict):
        return entity
    out = dict(entity)
    for micro_key, human_key in (
        ("daily_spend_cap", "daily_spend_cap_currency"),
        ("lifetime_spend_cap", "lifetime_spend_cap_currency"),
        ("budget_in_micro_currency", "budget_currency"),
        ("bid_in_micro_currency", "bid_currency"),
    ):
        if micro_key in out and out[micro_key] is not None:
            out[human_key] = from_micro(out[micro_key])
    return out
