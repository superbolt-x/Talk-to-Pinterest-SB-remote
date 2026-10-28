"""Pinterest API v5 REST plumbing — shared HTTP, auth headers, pagination.

Everything is plain REST/JSON over ``requests``. Pinterest paginates list
endpoints with an opaque ``bookmark``: each response carries a ``bookmark``
string, and you pass it back as ``?bookmark=`` to fetch the next page until it
comes back empty/absent.

Money note: Pinterest ads amounts (budgets, bids, and some analytics columns)
are expressed in MICRO-currency — 1 unit = 1/1,000,000 of the account currency.
Helpers here convert to/from the account currency (e.g. dollars).
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import requests

from pinloop.auth import PinterestAuthError, get_access_token, profile_names

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

logger = logging.getLogger("pinloop.client")

MICRO = 1_000_000

# How many pages a paginated read will walk before stopping, so a runaway
# account can't hang a tool call. Each page holds up to `page_size` items.
DEFAULT_PAGE_SIZE = 250
MAX_PAGES = 40


# ---------------------------------------------------------------------------
# Money conversion
# ---------------------------------------------------------------------------


def to_micro(amount: float | None) -> int | None:
    """Account-currency amount (e.g. dollars) -> integer micro-currency."""
    if amount is None:
        return None
    return int(round(float(amount) * MICRO))


def from_micro(micro: Any) -> float | None:
    """Integer micro-currency -> account-currency amount (e.g. dollars)."""
    if micro is None or micro == "":
        return None
    try:
        return int(micro) / MICRO
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Shared HTTP
# ---------------------------------------------------------------------------


def _request_with_retry(
    method: str,
    url: str,
    max_attempts: int = 5,
    base_delay: float = 1.5,
    **kwargs: Any,
) -> requests.Response:
    """requests.request with exponential backoff + jitter.

    Retries on network errors, 429, and 5xx (respecting Retry-After). Does NOT
    retry other 4xx — those need a code fix or a fresh token, not a retry.
    """
    last_exception: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(method, url, timeout=60, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exception = exc
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "Network error on attempt %d/%d (%s). Retrying in %.1fs.",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5)
            logger.warning(
                "Got HTTP %d on attempt %d/%d. Retrying in %.1fs. Body: %s",
                resp.status_code, attempt, max_attempts, delay, resp.text[:300],
            )
            time.sleep(delay)
            last_exception = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            continue

        return resp

    raise RuntimeError(
        f"Request to {url} failed after {max_attempts} attempts."
    ) from last_exception


def _headers(config: PinloopConfig, profile: str | None = None) -> dict:
    return {
        "Authorization": f"Bearer {get_access_token(config, profile=profile)}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Multi-login routing: ad account id -> profile (Pinterest login) that can see it
# ---------------------------------------------------------------------------

_ACCOUNT_PATH_RE = re.compile(r"^/?ad_accounts/(\d+)")
_account_profiles: dict[str, str] = {}
_map_lock = threading.Lock()


def discover_ad_accounts(config: PinloopConfig) -> tuple[list[dict], dict[str, str]]:
    """List ad accounts for every profile, tag each with the profile that returned
    it, and refresh the account->profile map used for routing.

    If two profiles see the same account the first profile in config order wins.
    A profile that fails (dead token, API error) is reported in the returned
    errors dict and skipped, so one broken login doesn't hide the others.
    """
    accounts: list[dict] = []
    seen: dict[str, str] = {}
    errors: dict[str, str] = {}
    for profile in profile_names(config):
        try:
            items = get_paginated(config, "/ad_accounts", max_items=500, profile=profile)
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            logger.warning("Ad account discovery failed for profile %s: %s", profile, exc)
            errors[profile] = str(exc)
            continue
        for a in items:
            acct_id = str(a.get("id"))
            if acct_id in seen:
                continue
            seen[acct_id] = profile
            accounts.append({**a, "profile": profile})
    with _map_lock:
        _account_profiles.clear()
        _account_profiles.update(seen)
    return accounts, errors


def _profile_for_path(config: PinloopConfig, path: str) -> str | None:
    """Pick the profile for a request. Account-scoped paths route to the login that
    owns the account; everything else uses the default (first) profile."""
    if len(profile_names(config)) < 2:
        return None
    m = _ACCOUNT_PATH_RE.match(path)
    if not m:
        return None
    acct_id = m.group(1)
    with _map_lock:
        profile = _account_profiles.get(acct_id)
    if profile is None:
        discover_ad_accounts(config)
        with _map_lock:
            profile = _account_profiles.get(acct_id)
    return profile


def api_request(
    config: PinloopConfig,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    profile: str | None = None,
) -> dict:
    """Call a Pinterest v5 endpoint and return the parsed JSON response.

    With several profiles configured, ``/ad_accounts/{id}/...`` paths are routed to
    the profile that owns the account; pass ``profile`` to force one. Retries once
    with a forced token refresh on 401 (access token invalidated mid-flight) and,
    for routed account paths, once on 403 after rebuilding the account map (access
    may have changed since it was built). Raises RuntimeError with the response
    body on other >=400 so tool callers surface actionable API messages.
    """
    url = f"{config.ads.api_base}/{path.lstrip('/')}"
    routed = profile is None and len(profile_names(config)) > 1
    if profile is None:
        profile = _profile_for_path(config, path)

    def send() -> requests.Response:
        return _request_with_retry(
            method, url, headers=_headers(config, profile), params=params, json=json_body
        )

    resp = send()

    if resp.status_code == 401:
        logger.warning("Got 401 on %s. Forcing token refresh and retrying once.", path)
        get_access_token(config, force_refresh=True, profile=profile)
        resp = send()

    if resp.status_code == 403 and routed and _ACCOUNT_PATH_RE.match(path):
        discover_ad_accounts(config)
        new_profile = _profile_for_path(config, path)
        if new_profile and new_profile != profile:
            logger.warning("Got 403 on %s with profile %s; retrying as %s.", path, profile, new_profile)
            profile = new_profile
            resp = send()

    if resp.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed: HTTP {resp.status_code} — {resp.text[:2000]}"
        )

    if not resp.content:
        return {}
    return resp.json()


def get_paginated(
    config: PinloopConfig,
    path: str,
    *,
    params: dict | None = None,
    max_items: int = 1000,
    profile: str | None = None,
) -> list[dict]:
    """Walk a Pinterest bookmark-paginated list endpoint and return all items.

    Stops at max_items or MAX_PAGES, whichever comes first. Logs a warning if
    the cap truncates results so a silent partial pull is never mistaken for a
    complete one.
    """
    params = dict(params or {})
    params.setdefault("page_size", DEFAULT_PAGE_SIZE)
    items: list[dict] = []
    bookmark = ""

    for page in range(MAX_PAGES):
        if bookmark:
            params["bookmark"] = bookmark
        data = api_request(config, "GET", path, params=params, profile=profile)
        items.extend(data.get("items", []))
        bookmark = data.get("bookmark") or ""
        if not bookmark or len(items) >= max_items:
            break
    else:
        logger.warning("Pagination hit MAX_PAGES=%d on %s — results truncated.", MAX_PAGES, path)

    if len(items) > max_items:
        logger.warning("Truncating %d items to max_items=%d on %s.", len(items), max_items, path)
        return items[:max_items]
    return items


__all__ = [
    "MICRO",
    "to_micro",
    "from_micro",
    "api_request",
    "get_paginated",
    "discover_ad_accounts",
    "PinterestAuthError",
]
