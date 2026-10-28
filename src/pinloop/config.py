"""Load Pinloop configuration from environment variables for remote deployment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


DEFAULT_PROFILE = "default"


def _parse_profiles(raw: str) -> dict[str, str]:
    """Parse PINTEREST_PROFILES: JSON object {"superbolt": "<refresh>", "sgs": "<refresh>"}."""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"PINTEREST_PROFILES is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in data.items()
    ):
        raise ValueError(
            'PINTEREST_PROFILES must be a JSON object of {"name": "refresh_token"} strings.'
        )
    return {k: v.strip() for k, v in data.items()}


@dataclass
class AdsConfig:
    app_id: str = ""               # Pinterest app (client) id
    app_secret: str = ""           # Pinterest app secret
    refresh_token_seed: str = ""   # long-lived seed; see auth.py for rotation notes
    access_token_seed: str = ""    # optional; runtime mints fresh tokens via refresh
    ad_account_id: str = ""        # optional default ad account (tools accept per-call)
    token_path: str = ""           # optional durable file (volume) for a rotated refresh token
    api_base: str = "https://api.pinterest.com/v5"
    # name -> refresh token, one per Pinterest login (see PINTEREST_PROFILES).
    # Order matters: the first profile wins when two logins see the same ad
    # account, and is the default for organic (boards/pins) calls.
    profiles: dict[str, str] = field(default_factory=dict)

    def profile_tokens(self) -> dict[str, str]:
        """Configured profiles, falling back to the single legacy token as "default"."""
        if self.profiles:
            return self.profiles
        if self.refresh_token_seed.strip() or self.token_path:
            return {DEFAULT_PROFILE: self.refresh_token_seed}
        return {}


@dataclass
class SafetyConfig:
    max_daily_budget: float = 50.0  # in account currency units (e.g. dollars), NOT micro
    require_dry_run: bool = True
    log_file: str = "/tmp/pinloop-audit.log"
    blocked_operations: list[str] = field(default_factory=list)


@dataclass
class PinloopConfig:
    ads: AdsConfig = field(default_factory=AdsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def load_config(config_path: str | None = None) -> PinloopConfig:
    """Load configuration from environment variables.

    config_path is ignored in remote mode — all settings come from env vars.
    """
    return PinloopConfig(
        ads=AdsConfig(
            app_id=os.environ.get("PINTEREST_APP_ID", ""),
            app_secret=os.environ.get("PINTEREST_APP_SECRET", ""),
            refresh_token_seed=os.environ.get("PINTEREST_REFRESH_TOKEN", ""),
            access_token_seed=os.environ.get("PINTEREST_ACCESS_TOKEN", ""),
            ad_account_id=os.environ.get("PINTEREST_AD_ACCOUNT_ID", ""),
            token_path=os.environ.get("PINTEREST_TOKEN_PATH", ""),
            api_base=os.environ.get(
                "PINTEREST_API_BASE", "https://api.pinterest.com/v5"
            ).rstrip("/"),
            profiles=_parse_profiles(os.environ.get("PINTEREST_PROFILES", "")),
        ),
        safety=SafetyConfig(
            max_daily_budget=float(os.environ.get("PINLOOP_MAX_DAILY_BUDGET", "50.0")),
            require_dry_run=os.environ.get("PINLOOP_REQUIRE_DRY_RUN", "true").lower()
            != "false",
            log_file=os.environ.get("PINLOOP_AUDIT_LOG", "/tmp/pinloop-audit.log"),
        ),
    )
