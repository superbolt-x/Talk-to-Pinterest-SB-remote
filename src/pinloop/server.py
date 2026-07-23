"""Pinloop MCP server — FastMCP instance with all Pinterest tool registrations."""

from __future__ import annotations

import functools
import logging
import os
import secrets
from typing import Callable
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from pinloop.config import load_config

logger = logging.getLogger("pinloop")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)

# ── Tool classification (drives Claude's permission management) ───────────────
# readOnlyHint -> safe reads; WRITE -> mutates Pinterest; DESTRUCTIVE ->
# deletes/archives or sends real conversion data. openWorldHint -> talks to
# Pinterest's API.
_READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

# ── Auth: authless (Parker-style) ─────────────────────────────────────────────
SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


def _derive_allowed_hosts() -> list:
    """Hosts allowed through DNS-rebinding protection: SERVER_URL (scheme optional)
    + Railway's auto-injected RAILWAY_PUBLIC_DOMAIN + optional MCP_ALLOWED_HOSTS.
    Prevents 421 'Invalid Host header'."""
    candidates: list = []
    if SERVER_URL:
        _u = SERVER_URL if "//" in SERVER_URL else "https://" + SERVER_URL
        netloc = urlparse(_u).netloc
        if netloc:
            candidates.append(netloc)
    rpd = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if rpd:
        candidates.append(rpd)
    for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(","):
        if h.strip():
            candidates.append(h.strip())
    seen, out = set(), []
    for h in candidates:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


_allowed_hosts = _derive_allowed_hosts()
if _allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[x for h in _allowed_hosts for x in (h, f"{h}:*")],
        allowed_origins=[x for h in _allowed_hosts for x in (f"https://{h}", f"http://{h}")],
    )
    logger.info("DNS-rebinding protection ON — allowed hosts: %s", _allowed_hosts)
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    logger.warning("No SERVER_URL / RAILWAY_PUBLIC_DOMAIN — DNS-rebinding protection OFF")

mcp = FastMCP(
    "Talk-to-Pinterest",
    instructions=(
        "Talk-to-Pinterest connects Pinterest to Claude — organic pins & boards, "
        "ads campaign management, and conversions (tags + Conversions API). Use "
        "the read tools to inspect boards/pins/campaigns and pull analytics; use "
        "the write tools (which return a preview you confirm with confirm_and_apply) "
        "to create or change things. Call list_ad_accounts first when the target ad "
        "account is not known."
    ),
    transport_security=_transport_security,
)
logger.info(
    "Talk-to-Pinterest configured — server_url=%s auth_gate=%s",
    SERVER_URL or "(unset)", bool(AUTH_TOKEN),
)


class TokenGateMiddleware:
    """Authless-with-a-shared-secret ASGI gate. When a token is configured, require
    it on every HTTP request (from ?access_token=/token or Bearer). Correct token →
    pass through (no auth challenge); otherwise 401."""

    def __init__(self, app, token: str):
        self._app = app
        self._token = token

    def _provided(self, scope) -> str:
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        val = (qs.get("access_token") or qs.get("token") or [""])[0]
        if val:
            return val
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
                if auth.lower().startswith("bearer "):
                    return auth[7:].strip()
        return ""

    _OAUTH_DISCOVERY_PREFIXES = (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/.well-known/openid-configuration",
    )

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if any(path.startswith(p) for p in self._OAUTH_DISCOVERY_PREFIXES):
                from starlette.responses import PlainTextResponse

                await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
                return
            if self._token and not secrets.compare_digest(self._provided(scope), self._token):
                from starlette.responses import JSONResponse

                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


_config = load_config()


def _safe(fn: Callable) -> Callable:
    """Wrap a tool function so exceptions return structured error dicts."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            from pinloop.auth import PinterestAuthError

            if isinstance(e, PinterestAuthError):
                return {
                    "error": "Authentication failed — Pinterest refresh token is dead.",
                    "hint": (
                        "The refresh token was revoked or expired. Re-run the "
                        "one-time OAuth consent flow (scripts/mint_seed_token.py) "
                        "and update PINTEREST_REFRESH_TOKEN (see README)."
                    ),
                    "detail": str(e),
                }
            return {"error": str(e), "tool": fn.__name__}

    return wrapper


# ===========================================================================
# Health
# ===========================================================================


@mcp.tool(annotations=_READONLY)
@_safe
def health_check() -> dict:
    """Test Talk-to-Pinterest connectivity — checks config, OAuth token, and the API.

    Run this first if other tools are failing. Returns status for each layer and
    actionable guidance if something is broken.
    """
    from pinloop.auth import PinterestAuthError, check_config, get_access_token

    status: dict = {"config": "ok", "auth": "unknown", "api": "unknown"}
    try:
        check_config(_config)
    except Exception as e:
        status["config"] = "error"
        status["config_error"] = str(e)
        return status

    try:
        get_access_token(_config)
        status["auth"] = "ok"
    except PinterestAuthError as e:
        status["auth"] = "error"
        status["auth_error"] = str(e)
        status["hint"] = "Re-run the OAuth consent flow and reset PINTEREST_REFRESH_TOKEN."
        return status
    except Exception as e:
        status["auth"] = "error"
        status["auth_error"] = str(e)
        return status

    try:
        from pinloop.api.accounts import get_user_account

        acct = get_user_account(_config)
        status["api"] = "ok"
        status["username"] = acct.get("username")
        status["default_ad_account_id"] = _config.ads.ad_account_id or "(unset)"
    except Exception as e:
        status["api"] = "error"
        status["api_error"] = str(e)

    return status


# ===========================================================================
# Accounts & date helper
# ===========================================================================


@mcp.tool(annotations=_READONLY)
@_safe
def get_user_account() -> dict:
    """Get the authenticated Pinterest account (username, account type, etc.)."""
    from pinloop.api.accounts import get_user_account as _impl

    return _impl(_config)


@mcp.tool(annotations=_READONLY)
@_safe
def list_ad_accounts() -> dict:
    """List every Pinterest ad account the authenticated user can access.

    Returns each account's id, name, country, and currency. Call this first to
    discover which ad account to pass as account_id to the campaign, analytics,
    and conversion tools.
    """
    from pinloop.api.accounts import list_ad_accounts as _impl

    return _impl(_config)


@mcp.tool(annotations=_READONLY)
@_safe
def split_date_range(
    date_range_start: str, date_range_end: str, days: int = 7
) -> dict:
    """Split an inclusive date range into consecutive, NON-overlapping windows.

    Pinterest analytics ranges are inclusive on both ends, so reusing an end date
    as the next window's start double-counts that day. Use this before week-over-
    week pulls. Date format "YYYY-MM-DD".
    """
    from pinloop.dates import split_date_range as _impl

    windows = _impl(date_range_start, date_range_end, days=days)
    return {
        "windows": [{"start": s, "end": e} for s, e in windows],
        "total_windows": len(windows),
        "days_per_window": days,
    }


# ===========================================================================
# Pins & Boards (read)
# ===========================================================================


@mcp.tool(annotations=_READONLY)
@_safe
def list_boards(privacy: str = "", max_items: int = 250) -> dict:
    """List boards owned by the authenticated account.

    privacy: optional filter — PUBLIC, PROTECTED, or SECRET.
    """
    from pinloop.api.boards import list_boards as _impl

    return _impl(_config, privacy=privacy, max_items=max_items)


@mcp.tool(annotations=_READONLY)
@_safe
def get_board(board_id: str) -> dict:
    """Get a single board by id (name, description, privacy, pin/follower counts)."""
    from pinloop.api.boards import get_board as _impl

    return _impl(_config, board_id)


@mcp.tool(annotations=_READONLY)
@_safe
def list_board_sections(board_id: str, max_items: int = 250) -> dict:
    """List the sections within a board."""
    from pinloop.api.boards import list_board_sections as _impl

    return _impl(_config, board_id, max_items=max_items)


@mcp.tool(annotations=_READONLY)
@_safe
def list_board_pins(board_id: str, max_items: int = 250) -> dict:
    """List the pins on a board."""
    from pinloop.api.boards import list_board_pins as _impl

    return _impl(_config, board_id, max_items=max_items)


@mcp.tool(annotations=_READONLY)
@_safe
def list_pins(max_items: int = 250) -> dict:
    """List pins owned by the authenticated account."""
    from pinloop.api.pins import list_pins as _impl

    return _impl(_config, max_items=max_items)


@mcp.tool(annotations=_READONLY)
@_safe
def get_pin(pin_id: str) -> dict:
    """Get a single pin by id (title, description, link, media, board)."""
    from pinloop.api.pins import get_pin as _impl

    return _impl(_config, pin_id)


@mcp.tool(annotations=_READONLY)
@_safe
def get_pin_analytics(
    pin_id: str,
    start_date: str,
    end_date: str,
    metric_types: list[str] | None = None,
) -> dict:
    """Organic analytics for one pin over an inclusive date range ("YYYY-MM-DD").

    metric_types defaults to IMPRESSION, PIN_CLICK, OUTBOUND_CLICK, SAVE,
    ENGAGEMENT. This is ORGANIC pin performance — for paid ad performance use
    the *_analytics campaign tools.
    """
    from pinloop.api.pins import get_pin_analytics as _impl

    return _impl(
        _config, pin_id, start_date=start_date, end_date=end_date, metric_types=metric_types
    )


# ===========================================================================
# Campaign management (read)
# ===========================================================================


@mcp.tool(annotations=_READONLY)
@_safe
def list_campaigns(
    account_id: str = "", entity_statuses: list[str] | None = None, max_items: int = 250
) -> dict:
    """List campaigns in an ad account.

    entity_statuses: optional filter — e.g. ["ACTIVE"], ["PAUSED"], ["ARCHIVED"].
    account_id defaults to PINTEREST_AD_ACCOUNT_ID if set. Budgets are shown in
    both micro-currency (Pinterest's raw field) and account currency.
    """
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import list_campaigns as _impl

    return _impl(
        _config,
        account_id=require_account(_config, account_id),
        entity_statuses=entity_statuses,
        max_items=max_items,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_campaign(campaign_id: str, account_id: str = "") -> dict:
    """Get a single campaign by id."""
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import get_campaign as _impl

    return _impl(_config, account_id=require_account(_config, account_id), campaign_id=campaign_id)


@mcp.tool(annotations=_READONLY)
@_safe
def list_ad_groups(
    account_id: str = "", campaign_ids: list[str] | None = None, max_items: int = 250
) -> dict:
    """List ad groups in an ad account, optionally filtered by campaign_ids."""
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import list_ad_groups as _impl

    return _impl(
        _config,
        account_id=require_account(_config, account_id),
        campaign_ids=campaign_ids,
        max_items=max_items,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def list_ads(
    account_id: str = "",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    max_items: int = 250,
) -> dict:
    """List ads in an ad account, optionally filtered by campaign or ad group."""
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import list_ads as _impl

    return _impl(
        _config,
        account_id=require_account(_config, account_id),
        campaign_ids=campaign_ids,
        ad_group_ids=ad_group_ids,
        max_items=max_items,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_campaign_analytics(
    campaign_ids: list[str],
    start_date: str,
    end_date: str,
    account_id: str = "",
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Paid performance for specific campaigns over an inclusive date range.

    campaign_ids is REQUIRED (Pinterest scopes analytics by entity id). Dates are
    "YYYY-MM-DD", inclusive. granularity: TOTAL | DAY | WEEK | MONTH | HOUR.
    columns default to a common set (SPEND_IN_DOLLAR, IMPRESSION_1, CLICKTHROUGH_1,
    OUTBOUND_CLICK_1, TOTAL_CONVERSIONS, CTR, CPC_IN_DOLLAR, ECPM_IN_DOLLAR) —
    pass your own Pinterest column names to override.
    """
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import get_campaign_analytics as _impl

    return _impl(
        _config, account_id=require_account(_config, account_id), campaign_ids=campaign_ids,
        start_date=start_date, end_date=end_date, columns=columns, granularity=granularity,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_ad_group_analytics(
    ad_group_ids: list[str],
    start_date: str,
    end_date: str,
    account_id: str = "",
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Paid performance for specific ad groups over an inclusive date range.

    ad_group_ids is REQUIRED. See get_campaign_analytics for column/granularity notes.
    """
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import get_ad_group_analytics as _impl

    return _impl(
        _config, account_id=require_account(_config, account_id), ad_group_ids=ad_group_ids,
        start_date=start_date, end_date=end_date, columns=columns, granularity=granularity,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_ad_analytics(
    ad_ids: list[str],
    start_date: str,
    end_date: str,
    account_id: str = "",
    columns: list[str] | None = None,
    granularity: str = "TOTAL",
) -> dict:
    """Paid performance for specific ads over an inclusive date range.

    ad_ids is REQUIRED. See get_campaign_analytics for column/granularity notes.
    """
    from pinloop.api.accounts import require_account
    from pinloop.api.campaigns import get_ad_analytics as _impl

    return _impl(
        _config, account_id=require_account(_config, account_id), ad_ids=ad_ids,
        start_date=start_date, end_date=end_date, columns=columns, granularity=granularity,
    )


# ===========================================================================
# Conversions (read)
# ===========================================================================


@mcp.tool(annotations=_READONLY)
@_safe
def list_conversion_tags(account_id: str = "") -> dict:
    """List conversion tags configured on an ad account."""
    from pinloop.api.accounts import require_account
    from pinloop.api.conversions import list_conversion_tags as _impl

    return _impl(_config, account_id=require_account(_config, account_id))


@mcp.tool(annotations=_READONLY)
@_safe
def get_conversion_tag(conversion_tag_id: str, account_id: str = "") -> dict:
    """Get a single conversion tag by id."""
    from pinloop.api.accounts import require_account
    from pinloop.api.conversions import get_conversion_tag as _impl

    return _impl(
        _config, account_id=require_account(_config, account_id), conversion_tag_id=conversion_tag_id
    )


# ===========================================================================
# Write tools (safety layer — draft returns a preview, confirm_and_apply executes)
# ===========================================================================


@mcp.tool(annotations=_WRITE)
@_safe
def draft_create_board(name: str, description: str = "", privacy: str = "PUBLIC") -> dict:
    """Draft creating a board — returns a PREVIEW, does NOT create anything.

    privacy: PUBLIC | PROTECTED | SECRET. Call confirm_and_apply with the
    returned plan_id and dry_run=false to execute.
    """
    from pinloop.mutations import draft_create_board as _impl

    return _impl(_config, name=name, description=description, privacy=privacy)


@mcp.tool(annotations=_DESTRUCTIVE)
@_safe
def draft_delete_board(board_id: str) -> dict:
    """Draft DELETING a board — returns a PREVIEW. IRREVERSIBLE (removes its pins too).

    Requires double confirmation. Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_delete_board as _impl

    return _impl(_config, board_id=board_id)


@mcp.tool(annotations=_WRITE)
@_safe
def draft_create_pin(
    board_id: str,
    image_url: str,
    title: str = "",
    description: str = "",
    link: str = "",
    board_section_id: str = "",
    alt_text: str = "",
) -> dict:
    """Draft creating an image pin from a public image URL — returns a PREVIEW.

    board_id and image_url are required. image_url must be publicly reachable by
    Pinterest. Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_create_pin as _impl

    return _impl(
        _config, board_id=board_id, image_url=image_url, title=title,
        description=description, link=link, board_section_id=board_section_id, alt_text=alt_text,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
@_safe
def draft_delete_pin(pin_id: str) -> dict:
    """Draft DELETING a pin — returns a PREVIEW. IRREVERSIBLE.

    Requires double confirmation. Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_delete_pin as _impl

    return _impl(_config, pin_id=pin_id)


@mcp.tool(annotations=_WRITE)
@_safe
def draft_create_campaign(
    name: str,
    objective_type: str,
    account_id: str = "",
    status: str = "PAUSED",
    daily_budget: float = 0,
    lifetime_budget: float = 0,
) -> dict:
    """Draft creating a campaign — returns a PREVIEW, does NOT create anything.

    objective_type: AWARENESS | CONSIDERATION | VIDEO_VIEW | WEB_CONVERSION |
    CATALOG_SALES | WEB_SESSIONS. status defaults to PAUSED (safe). Budgets are
    in ACCOUNT CURRENCY (e.g. dollars), not micro — provide daily_budget or
    lifetime_budget. daily_budget is checked against PINLOOP_MAX_DAILY_BUDGET.
    Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_create_campaign as _impl

    return _impl(
        _config, account_id=account_id, name=name, objective_type=objective_type,
        status=status, daily_budget=daily_budget, lifetime_budget=lifetime_budget,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_create_ad_group(
    campaign_id: str,
    name: str,
    account_id: str = "",
    status: str = "PAUSED",
    budget: float = 0,
    bid: float = 0,
    billable_event: str = "IMPRESSION",
) -> dict:
    """Draft creating an ad group under a campaign — returns a PREVIEW.

    status defaults to PAUSED. budget and bid are in ACCOUNT CURRENCY (dollars),
    not micro. billable_event: IMPRESSION | CLICKTHROUGH | VIDEO_V_50_MRC etc.
    Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_create_ad_group as _impl

    return _impl(
        _config, account_id=account_id, campaign_id=campaign_id, name=name,
        status=status, budget=budget, bid=bid, billable_event=billable_event,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_update_status(
    entity_type: str, entity_id: str, status: str, account_id: str = ""
) -> dict:
    """Draft changing a campaign/ad_group/ad status — returns a PREVIEW.

    entity_type: "campaign" | "ad_group" | "ad". status: ACTIVE | PAUSED |
    ARCHIVED. Use this to pause or resume. ARCHIVED requires double confirmation.
    Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_update_status as _impl

    return _impl(
        _config, account_id=account_id, entity_type=entity_type, entity_id=entity_id, status=status
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_create_conversion_tag(name: str, account_id: str = "", aem_enabled: bool = False) -> dict:
    """Draft creating a conversion tag on an ad account — returns a PREVIEW.

    Call confirm_and_apply with dry_run=false to execute.
    """
    from pinloop.mutations import draft_create_conversion_tag as _impl

    return _impl(_config, account_id=account_id, name=name, aem_enabled=aem_enabled)


@mcp.tool(annotations=_WRITE)
@_safe
def draft_send_conversion_event(
    event_name: str,
    action_source: str,
    event_time: int,
    user_data: dict,
    account_id: str = "",
    custom_data: dict | None = None,
    event_id: str = "",
    test: bool = False,
) -> dict:
    """Draft sending a server-side conversion event (Conversions API) — returns a PREVIEW.

    event_name: e.g. "checkout", "add_to_cart", "page_visit", "signup", "lead".
    action_source: "web" | "app_android" | "app_ios" | "offline".
    event_time: UNIX timestamp in SECONDS.
    user_data: identifiers dict, e.g. {"em": "user@example.com", "ph": "+15551234567",
      "client_ip_address": "...", "client_user_agent": "..."}. Email/phone/name
      fields are SHA-256 hashed locally before sending — raw PII is NOT echoed in
      the preview. custom_data: e.g. {"currency": "USD", "value": "49.99",
      "order_id": "..."}. Set test=true to route to Pinterest's test pipeline.
    Non-test events require double confirmation. Call confirm_and_apply with
    dry_run=false to execute.
    """
    from pinloop.mutations import draft_send_conversion_event as _impl

    return _impl(
        _config, account_id=account_id, event_name=event_name, action_source=action_source,
        event_time=event_time, user_data=user_data, custom_data=custom_data,
        event_id=event_id, test=test,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
@_safe
def confirm_and_apply(plan_id: str, dry_run: bool = True) -> dict:
    """Execute a previously previewed change (from any draft_* tool).

    IMPORTANT: defaults to dry_run=True. You MUST explicitly pass dry_run=false to
    make real changes to Pinterest. The plan_id comes from a prior draft_* call;
    plans expire when the server restarts.
    """
    from pinloop.mutations import confirm_and_apply as _impl

    return _impl(_config, plan_id=plan_id, dry_run=dry_run)


# ===========================================================================
# HTTP server entrypoint
# ===========================================================================


def main():
    """Run the MCP server.

    MCP_TRANSPORT: streamable-http (default) | sse | stdio.
    HTTP env vars (ignored for stdio): MCP_HOST (default 0.0.0.0), MCP_PORT (default 8000).
    """
    import uvicorn

    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    logger.info("Starting Talk-to-Pinterest MCP server transport=%s", transport)

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("MCP_PORT", "8000"))

    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    raw_app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()
    gated_app = TokenGateMiddleware(raw_app, token=AUTH_TOKEN)

    @asynccontextmanager
    async def lifespan(_app):
        async with raw_app.router.lifespan_context(_app):
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "transport": transport, "auth_gate": bool(AUTH_TOKEN)})

    routes = [Route("/health", health), Mount("/", app=gated_app)]
    app = Starlette(lifespan=lifespan, routes=routes)
    logger.info("Listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
