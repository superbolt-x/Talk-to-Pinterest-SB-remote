"""Load Pinloop configuration from environment variables for remote deployment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AdsConfig:
    app_id: str = ""               # Pinterest app (client) id
    app_secret: str = ""           # Pinterest app secret
    refresh_token_seed: str = ""   # long-lived seed; see auth.py for rotation notes
    access_token_seed: str = ""    # optional; runtime mints fresh tokens via refresh
    ad_account_id: str = ""        # optional default ad account (tools accept per-call)
    token_path: str = ""           # optional durable file (volume) for a rotated refresh token
    api_base: str = "https://api.pinterest.com/v5"


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
        ),
        safety=SafetyConfig(
            max_daily_budget=float(os.environ.get("PINLOOP_MAX_DAILY_BUDGET", "50.0")),
            require_dry_run=os.environ.get("PINLOOP_REQUIRE_DRY_RUN", "true").lower()
            != "false",
            log_file=os.environ.get("PINLOOP_AUDIT_LOG", "/tmp/pinloop-audit.log"),
        ),
    )
