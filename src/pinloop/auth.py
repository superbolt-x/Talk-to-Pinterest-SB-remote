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

Multiple logins: ``PINTEREST_PROFILES`` holds one refresh token per Pinterest
login (same app id/secret). Every cache, seed and rotation file below is keyed by
profile name; with only ``PINTEREST_REFRESH_TOKEN`` set there is a single
"default" profile and behaviour is unchanged.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pinloop.config import DEFAULT_PROFILE

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


_cached_tokens: dict[str, TokenBundle] = {}
_lock = threading.Lock()


def profile_names(config: PinloopConfig) -> list[str]:
    """Configured profile names, in priority order."""
    return list(config.ads.profile_tokens())


def resolve_profile(config: PinloopConfig, profile: str | None) -> str:
    """Validate a profile name, defaulting to the first configured profile."""
    names = profile_names(config)
    if not names:
        raise ConfigError("No Pinterest profile configured (set PINTEREST_REFRESH_TOKEN or PINTEREST_PROFILES).")
    if not profile:
        return names[0]
    if profile not in names:
        raise ConfigError(f"Unknown Pinterest profile '{profile}'. Configured: {', '.join(names)}.")
    return profile


def _token_file(config: PinloopConfig, profile: str) -> str:
    """Rotation file for a profile. The legacy default profile uses PINTEREST_TOKEN_PATH
    as-is; named profiles get a per-profile suffix so logins never share a file."""
    if not config.ads.token_path:
        return ""
    if profile == DEFAULT_PROFILE:
        return config.ads.token_path
    return f"{config.ads.token_path}.{profile}"


def _token_url(config: PinloopConfig) -> str:
    return f"{config.ads.api_base}/oauth/token"


def _load_refresh_token(config: PinloopConfig, profile: str) -> str:
    """Read the live refresh token: volume file first (if configured), env seed otherwise."""
    token_file = _token_file(config, profile)
    if token_file:
        try:
            token = Path(token_file).read_text().strip()
            if token:
                return token
        except FileNotFoundError:
            pass

    seed = config.ads.profile_tokens().get(profile, "").strip()
    if seed:
        return seed

    raise ConfigError(
        f"No refresh token available for profile '{profile}': PINTEREST_REFRESH_TOKEN"
        "/PINTEREST_PROFILES is not set"
        + (
            f" and {token_file} does not exist"
            if token_file
            else ""
        )
        + ". Run the one-time OAuth consent flow (see README / "
        "scripts/mint_seed_token.py) and set PINTEREST_REFRESH_TOKEN."
    )


def _save_refresh_token(config: PinloopConfig, profile: str, token: str) -> None:
    """Persist a rotated refresh token durably (write-then-rename). No-op if
    PINTEREST_TOKEN_PATH is unset — Pinterest does not rotate by default."""
    token_file = _token_file(config, profile)
    if not token_file:
        return
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token)
    import os

    os.replace(tmp, path)


def _refresh_access_token(
    config: PinloopConfig, profile: str, refresh_token: str
) -> TokenBundle:
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
            token_file = _token_file(config, profile)
            raise PinterestAuthError(
                f"Pinterest refresh token for profile '{profile}' is no longer valid "
                "(expired or revoked). Re-run the one-time OAuth consent flow "
                "(scripts/mint_seed_token.py) logged in as that profile and update "
                "PINTEREST_REFRESH_TOKEN / PINTEREST_PROFILES"
                + (
                    f" (and delete the stale token file at {token_file})"
                    if token_file
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
        logger.info("Refresh token rotated by Pinterest (%s) — persisting new token.", profile)
        _save_refresh_token(config, profile, new_refresh)
    logger.info(
        "Access token refreshed (%s). Valid for ~%s seconds.", profile, payload.get("expires_in")
    )
    return bundle


def get_access_token(
    config: PinloopConfig, force_refresh: bool = False, profile: str | None = None
) -> str:
    """Return a valid access token for a profile, refreshing only when needed
    (cached in-process). ``profile`` defaults to the first configured profile.

    Thread-safe: FastMCP may serve concurrent tool calls.
    """
    profile = resolve_profile(config, profile)

    with _lock:
        cached = _cached_tokens.get(profile)
        if not force_refresh and cached and time.time() < cached.expires_at:
            return cached.access_token

        current = cached.refresh_token if cached else _load_refresh_token(config, profile)
        _cached_tokens[profile] = _refresh_access_token(config, profile, current)
        return _cached_tokens[profile].access_token


def check_config(config: PinloopConfig) -> None:
    """Raise ConfigError listing any missing required credentials."""
    missing = []
    if not config.ads.app_id:
        missing.append("PINTEREST_APP_ID")
    if not config.ads.app_secret:
        missing.append("PINTEREST_APP_SECRET")
    if not config.ads.profile_tokens():
        missing.append("PINTEREST_REFRESH_TOKEN (or PINTEREST_PROFILES)")
    if missing:
        raise ConfigError(
            f"Missing required environment variables: {', '.join(missing)}."
        )
