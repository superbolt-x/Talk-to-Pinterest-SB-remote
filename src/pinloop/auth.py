"""Pinterest API v5 OAuth — access-token refresh.

Pinterest uses "continuous refresh": exchanging the refresh token for a new
access token does NOT rotate the refresh token by default. The same refresh
token keeps working (access tokens last ~30 days, refresh tokens ~1 year). So,
unlike the Bing Ads server, the refresh token can live in a static env var
(``PINTEREST_REFRESH_TOKEN``) and NO mounted volume is required.

Robustness against rotation: if an app is configured with refresh-token
rotation, the token endpoint returns a NEW ``refresh_token`` on each refresh.
When ``PINTEREST_TOKEN_PATH`` is set we persist that rotated token to the file
(a Railway volume) so it survives restarts; without it we keep the rotated token
in-process only and fall back to the env seed on restart. Either way the
in-process cache always uses the most recent refresh token, so a single running
process never invalidates its own chain.

Access tokens are cached in-process with a safety margin, so the token endpoint
is hit roughly once per token lifetime, not once per tool call.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pinloop.config import PinloopConfig

logger = logging.getLogger("pinloop.auth")


class ConfigError(RuntimeError):
    pass


class PinterestAuthError(RuntimeError):
    """Raised when auth fails in a way that requires re-running the consent flow."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float  # unix timestamp (already includes a safety margin)


_cached_token: TokenBundle | None = None
_lock = threading.Lock()


def _token_url(config: PinloopConfig) -> str:
    return f"{config.ads.api_base}/oauth/token"


def _load_refresh_token(config: PinloopConfig) -> str:
    """Read the live refresh token: volume file first (if configured), env seed otherwise."""
    if config.ads.token_path:
        try:
            token = Path(config.ads.token_path).read_text().strip()
            if token:
                return token
        except FileNotFoundError:
            pass

    seed = config.ads.refresh_token_seed.strip()
    if seed:
        return seed

    raise ConfigError(
        "No refresh token available: PINTEREST_REFRESH_TOKEN is not set"
        + (
            f" and {config.ads.token_path} does not exist"
            if config.ads.token_path
            else ""
        )
        + ". Run the one-time OAuth consent flow (see README / "
        "scripts/mint_seed_token.py) and set PINTEREST_REFRESH_TOKEN."
    )


def _save_refresh_token(config: PinloopConfig, token: str) -> None:
    """Persist a rotated refresh token durably (write-then-rename). No-op if
    PINTEREST_TOKEN_PATH is unset — Pinterest does not rotate by default."""
    if not config.ads.token_path:
        return
    path = Path(config.ads.token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token)
    import os

    os.replace(tmp, path)


def _refresh_access_token(config: PinloopConfig, refresh_token: str) -> TokenBundle:
    """Exchange a refresh token for a new access token via POST /v5/oauth/token.

    Uses HTTP Basic auth (app_id:app_secret) per Pinterest's spec. Raises
    PinterestAuthError when the refresh token itself is dead (expired/revoked),
    which requires re-running the browser consent flow.
    """
    import requests

    basic = base64.b64encode(
        f"{config.ads.app_id}:{config.ads.app_secret}".encode()
    ).decode()
    resp = requests.post(
        _token_url(config),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )

    if resp.status_code in (400, 401):
        body = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        # Pinterest signals a dead refresh token via code 28 ("Refresh token is
        # expired") or an invalid_grant-style message.
        msg = str(body.get("message", "")).lower()
        if body.get("code") == 28 or "refresh token" in msg or "invalid_grant" in msg:
            raise PinterestAuthError(
                "Pinterest refresh token is no longer valid (expired or revoked). "
                "Re-run the one-time OAuth consent flow (scripts/mint_seed_token.py) "
                "and update PINTEREST_REFRESH_TOKEN"
                + (
                    f" (and delete the stale token file at {config.ads.token_path})"
                    if config.ads.token_path
                    else ""
                )
                + f". API said: {body or resp.text[:300]}"
            )
    resp.raise_for_status()

    payload = resp.json()
    # Pinterest returns a new refresh_token only when rotation is enabled;
    # otherwise reuse the one we sent.
    new_refresh = payload.get("refresh_token") or refresh_token
    bundle = TokenBundle(
        access_token=payload["access_token"],
        refresh_token=new_refresh,
        expires_at=time.time() + int(payload.get("expires_in", 2592000)) - 60,
    )
    if new_refresh != refresh_token:
        logger.info("Refresh token rotated by Pinterest — persisting new token.")
        _save_refresh_token(config, new_refresh)
    logger.info(
        "Access token refreshed. Valid for ~%s seconds.", payload.get("expires_in")
    )
    return bundle


def get_access_token(config: PinloopConfig, force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing only when needed (cached in-process).

    Thread-safe: FastMCP may serve concurrent tool calls.
    """
    global _cached_token

    with _lock:
        if (
            not force_refresh
            and _cached_token
            and time.time() < _cached_token.expires_at
        ):
            return _cached_token.access_token

        current = (
            _cached_token.refresh_token
            if _cached_token
            else _load_refresh_token(config)
        )
        _cached_token = _refresh_access_token(config, current)
        return _cached_token.access_token


def check_config(config: PinloopConfig) -> None:
    """Raise ConfigError listing any missing required credentials."""
    missing = []
    if not config.ads.app_id:
        missing.append("PINTEREST_APP_ID")
    if not config.ads.app_secret:
        missing.append("PINTEREST_APP_SECRET")
    if not config.ads.refresh_token_seed and not config.ads.token_path:
        missing.append("PINTEREST_REFRESH_TOKEN")
    if missing:
        raise ConfigError(
            f"Missing required environment variables: {', '.join(missing)}."
        )
