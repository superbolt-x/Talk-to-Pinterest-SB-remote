"""Write tools — all behind the preview/confirm safety layer.

Every draft_* function validates inputs, builds a ChangePlan, stores it, and
returns a preview. Nothing touches Pinterest until confirm_and_apply is called
with the plan_id and dry_run=False.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pinloop.api import boards as boards_api
from pinloop.api import campaigns as campaigns_api
from pinloop.api import conversions as conversions_api
from pinloop.api import pins as pins_api
from pinloop.api.accounts import require_account
from pinloop.safety.guards import (
    SafetyViolation,
    check_blocked_operation,
    check_budget_cap,
)
from pinloop.safety.preview import ChangePlan, get_plan, remove_plan, store_plan

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

_STATUS_ENTITY_TYPES = {"campaign", "ad_group", "ad"}


def _blocked(config: PinloopConfig, operation: str) -> dict | None:
    try:
        check_blocked_operation(operation, config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}
    return None


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------


def draft_create_board(
    config: PinloopConfig,
    *,
    name: str,
    description: str = "",
    privacy: str = "PUBLIC",
) -> dict:
    if err := _blocked(config, "create_board"):
        return err
    errors = []
    if not name or not name.strip():
        errors.append("name is required")
    errors += boards_api.validate_privacy(privacy)
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_board",
        entity_type="board",
        changes={"name": name, "description": description, "privacy": privacy.upper()},
    )
    store_plan(plan)
    return plan.to_preview()


def draft_delete_board(config: PinloopConfig, *, board_id: str) -> dict:
    if err := _blocked(config, "delete_board"):
        return err
    if not board_id:
        return {"error": "Validation failed", "details": ["board_id is required"]}

    plan = ChangePlan(
        operation="delete_board",
        entity_type="board",
        entity_id=board_id,
        changes={"action": "DELETE", "board_id": board_id},
        requires_double_confirm=True,
    )
    store_plan(plan)
    return plan.to_preview()


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


def draft_create_pin(
    config: PinloopConfig,
    *,
    board_id: str,
    image_url: str,
    title: str = "",
    description: str = "",
    link: str = "",
    board_section_id: str = "",
    alt_text: str = "",
) -> dict:
    if err := _blocked(config, "create_pin"):
        return err
    errors = []
    if not board_id:
        errors.append("board_id is required")
    if not image_url:
        errors.append("image_url is required (a publicly reachable image URL)")
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_pin",
        entity_type="pin",
        entity_id=board_id,
        changes={
            "board_id": board_id,
            "image_url": image_url,
            "title": title,
            "description": description,
            "link": link,
            "board_section_id": board_section_id,
            "alt_text": alt_text,
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_delete_pin(config: PinloopConfig, *, pin_id: str) -> dict:
    if err := _blocked(config, "delete_pin"):
        return err
    if not pin_id:
        return {"error": "Validation failed", "details": ["pin_id is required"]}

    plan = ChangePlan(
        operation="delete_pin",
        entity_type="pin",
        entity_id=pin_id,
        changes={"action": "DELETE", "pin_id": pin_id},
        requires_double_confirm=True,
    )
    store_plan(plan)
    return plan.to_preview()


# ---------------------------------------------------------------------------
# Campaign management
# ---------------------------------------------------------------------------


def draft_create_campaign(
    config: PinloopConfig,
    *,
    account_id: str = "",
    name: str = "",
    objective_type: str = "",
    status: str = "PAUSED",
    daily_budget: float = 0,
    lifetime_budget: float = 0,
) -> dict:
    if err := _blocked(config, "create_campaign"):
        return err

    errors = []
    if not name or not name.strip():
        errors.append("name is required")
    if objective_type.upper() not in campaigns_api.VALID_OBJECTIVES:
        errors.append(
            f"objective_type must be one of {sorted(campaigns_api.VALID_OBJECTIVES)}, "
            f"got '{objective_type}'"
        )
    if status.upper() not in campaigns_api.VALID_STATUSES:
        errors.append(
            f"status must be one of {sorted(campaigns_api.VALID_STATUSES)}, got '{status}'"
        )
    if daily_budget <= 0 and lifetime_budget <= 0:
        errors.append("Provide daily_budget or lifetime_budget (in account currency)")
    if errors:
        return {"error": "Validation failed", "details": errors}

    if daily_budget > 0:
        try:
            check_budget_cap(daily_budget, config.safety)
        except SafetyViolation as e:
            return {"error": str(e)}

    plan = ChangePlan(
        operation="create_campaign",
        entity_type="campaign",
        account_id=require_account(config, account_id),
        changes={
            "name": name,
            "objective_type": objective_type.upper(),
            "status": status.upper(),
            "daily_budget": daily_budget or None,
            "lifetime_budget": lifetime_budget or None,
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_create_ad_group(
    config: PinloopConfig,
    *,
    account_id: str = "",
    campaign_id: str = "",
    name: str = "",
    status: str = "PAUSED",
    budget: float = 0,
    bid: float = 0,
    billable_event: str = "IMPRESSION",
) -> dict:
    if err := _blocked(config, "create_ad_group"):
        return err

    errors = []
    if not campaign_id:
        errors.append("campaign_id is required")
    if not name or not name.strip():
        errors.append("name is required")
    if status.upper() not in campaigns_api.VALID_STATUSES:
        errors.append(
            f"status must be one of {sorted(campaigns_api.VALID_STATUSES)}, got '{status}'"
        )
    if errors:
        return {"error": "Validation failed", "details": errors}

    if budget > 0:
        try:
            check_budget_cap(budget, config.safety)
        except SafetyViolation as e:
            return {"error": str(e)}

    plan = ChangePlan(
        operation="create_ad_group",
        entity_type="ad_group",
        entity_id=campaign_id,
        account_id=require_account(config, account_id),
        changes={
            "campaign_id": campaign_id,
            "name": name,
            "status": status.upper(),
            "budget": budget or None,
            "bid": bid or None,
            "billable_event": billable_event.upper(),
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_update_status(
    config: PinloopConfig,
    *,
    account_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    status: str = "",
) -> dict:
    if err := _blocked(config, "update_status"):
        return err

    errors = []
    if entity_type not in _STATUS_ENTITY_TYPES:
        errors.append(
            f"entity_type must be one of {sorted(_STATUS_ENTITY_TYPES)}, got '{entity_type}'"
        )
    if not entity_id:
        errors.append("entity_id is required")
    if status.upper() not in campaigns_api.VALID_STATUSES:
        errors.append(
            f"status must be one of {sorted(campaigns_api.VALID_STATUSES)}, got '{status}'"
        )
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="update_status",
        entity_type=entity_type,
        entity_id=entity_id,
        account_id=require_account(config, account_id),
        changes={"status": status.upper()},
        requires_double_confirm=(status.upper() == "ARCHIVED"),
    )
    store_plan(plan)
    return plan.to_preview()


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def draft_create_conversion_tag(
    config: PinloopConfig,
    *,
    account_id: str = "",
    name: str = "",
    aem_enabled: bool = False,
) -> dict:
    if err := _blocked(config, "create_conversion_tag"):
        return err
    if not name or not name.strip():
        return {"error": "Validation failed", "details": ["name is required"]}

    plan = ChangePlan(
        operation="create_conversion_tag",
        entity_type="conversion_tag",
        account_id=require_account(config, account_id),
        changes={"name": name, "aem_enabled": aem_enabled},
    )
    store_plan(plan)
    return plan.to_preview()


def draft_send_conversion_event(
    config: PinloopConfig,
    *,
    account_id: str = "",
    event_name: str = "",
    action_source: str = "",
    event_time: int = 0,
    user_data: dict | None = None,
    custom_data: dict | None = None,
    event_id: str = "",
    test: bool = False,
) -> dict:
    if err := _blocked(config, "send_conversion_event"):
        return err

    errors = []
    if not event_name:
        errors.append("event_name is required (e.g. 'checkout', 'add_to_cart', 'page_visit')")
    if action_source not in conversions_api.VALID_ACTION_SOURCES:
        errors.append(
            f"action_source must be one of {sorted(conversions_api.VALID_ACTION_SOURCES)}, "
            f"got '{action_source}'"
        )
    if not event_time:
        errors.append("event_time is required (UNIX timestamp in seconds)")
    if not user_data:
        errors.append("user_data is required (at least one identifier, e.g. {'em': 'user@x.com'})")
    if errors:
        return {"error": "Validation failed", "details": errors}

    # Preview shows which user_data keys will be sent, but NOT raw PII values.
    plan = ChangePlan(
        operation="send_conversion_event",
        entity_type="conversion_event",
        account_id=require_account(config, account_id),
        changes={
            "event_name": event_name,
            "action_source": action_source,
            "event_time": int(event_time),
            "user_data_keys": sorted((user_data or {}).keys()),
            "custom_data": custom_data or {},
            "event_id": event_id,
            "test": test,
            "_user_data": user_data,  # consumed at apply; hidden from the human preview
        },
        requires_double_confirm=not test,
    )
    store_plan(plan)
    preview = plan.to_preview()
    # Don't echo raw PII back to the model/user.
    preview["changes"] = {k: v for k, v in preview["changes"].items() if k != "_user_data"}
    return preview


# ---------------------------------------------------------------------------
# confirm_and_apply — the ONLY function that mutates Pinterest
# ---------------------------------------------------------------------------


def confirm_and_apply(
    config: PinloopConfig, *, plan_id: str = "", dry_run: bool = True
) -> dict:
    """Execute a previously previewed change.

    Defaults to dry_run=True. The caller must explicitly pass dry_run=False to
    make real changes.
    """
    from pinloop.safety.audit import log_mutation

    plan = get_plan(plan_id)
    if plan is None:
        return {
            "error": f"No pending plan found with id '{plan_id}'. "
            "Plans expire when the MCP server restarts.",
        }

    if config.safety.require_dry_run:
        dry_run = True

    audit_changes = {k: v for k, v in plan.changes.items() if k != "_user_data"}

    if dry_run:
        log_mutation(
            config.safety.log_file,
            operation=plan.operation,
            account_id=plan.account_id,
            entity_type=plan.entity_type,
            entity_id=plan.entity_id,
            changes=audit_changes,
            dry_run=True,
            result="dry_run_success",
        )
        return {
            "status": "DRY_RUN_SUCCESS",
            "plan_id": plan.plan_id,
            "operation": plan.operation,
            "changes": audit_changes,
            "message": (
                "Dry run completed — no changes were made to Pinterest. To apply "
                "for real, call confirm_and_apply again with dry_run=false."
            ),
        }

    try:
        result = _execute_plan(config, plan)
    except Exception as e:
        log_mutation(
            config.safety.log_file,
            operation=plan.operation,
            account_id=plan.account_id,
            entity_type=plan.entity_type,
            entity_id=plan.entity_id,
            changes=audit_changes,
            dry_run=False,
            result="error",
            error=str(e),
        )
        return {"error": str(e), "plan_id": plan.plan_id}

    log_mutation(
        config.safety.log_file,
        operation=plan.operation,
        account_id=plan.account_id,
        entity_type=plan.entity_type,
        entity_id=plan.entity_id,
        changes=audit_changes,
        dry_run=False,
        result="success",
    )
    remove_plan(plan.plan_id)
    return {
        "status": "APPLIED",
        "plan_id": plan.plan_id,
        "operation": plan.operation,
        "result": result,
    }


def _execute_plan(config: PinloopConfig, plan: ChangePlan) -> dict:
    """Dispatch a confirmed plan to the right executor."""
    c = plan.changes
    op = plan.operation

    if op == "create_board":
        return boards_api.create_board(
            config, name=c["name"], description=c.get("description", ""),
            privacy=c.get("privacy", "PUBLIC"),
        )
    if op == "delete_board":
        return boards_api.delete_board(config, board_id=c["board_id"])

    if op == "create_pin":
        return pins_api.create_pin(
            config, board_id=c["board_id"], image_url=c["image_url"],
            title=c.get("title", ""), description=c.get("description", ""),
            link=c.get("link", ""), board_section_id=c.get("board_section_id", ""),
            alt_text=c.get("alt_text", ""),
        )
    if op == "delete_pin":
        return pins_api.delete_pin(config, pin_id=c["pin_id"])

    if op == "create_campaign":
        return campaigns_api.create_campaign(
            config, account_id=plan.account_id, name=c["name"],
            objective_type=c["objective_type"], status=c["status"],
            daily_budget=c.get("daily_budget"), lifetime_budget=c.get("lifetime_budget"),
        )
    if op == "create_ad_group":
        return campaigns_api.create_ad_group(
            config, account_id=plan.account_id, campaign_id=c["campaign_id"],
            name=c["name"], status=c["status"], budget=c.get("budget"),
            bid=c.get("bid"), billable_event=c.get("billable_event", "IMPRESSION"),
        )
    if op == "update_status":
        return campaigns_api.update_entity_status(
            config, account_id=plan.account_id, entity_type=plan.entity_type,
            entity_id=plan.entity_id, status=c["status"],
        )

    if op == "create_conversion_tag":
        return conversions_api.create_conversion_tag(
            config, account_id=plan.account_id, name=c["name"],
            aem_enabled=c.get("aem_enabled"),
        )
    if op == "send_conversion_event":
        return conversions_api.send_conversion_event(
            config, account_id=plan.account_id, event_name=c["event_name"],
            action_source=c["action_source"], event_time=c["event_time"],
            user_data=c.get("_user_data"), custom_data=c.get("custom_data"),
            event_id=c.get("event_id", ""), test=c.get("test", False),
        )

    raise ValueError(f"Unknown operation: {op}")
