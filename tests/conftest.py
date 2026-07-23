"""Shared test fixtures."""

import pytest

from pinloop.config import AdsConfig, PinloopConfig, SafetyConfig


@pytest.fixture
def config(tmp_path):
    """A PinloopConfig with test credentials and dry-run DISABLED (so apply tests
    can exercise the real path). Individual tests re-enable it if needed."""
    return PinloopConfig(
        ads=AdsConfig(
            app_id="1234567",
            app_secret="secret",
            refresh_token_seed="pinr.seed",
            ad_account_id="",
            api_base="https://api.pinterest.com/v5",
        ),
        safety=SafetyConfig(
            max_daily_budget=50.0,
            require_dry_run=False,
            log_file=str(tmp_path / "audit.log"),
        ),
    )
