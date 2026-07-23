"""Safety guards — budget caps and blocked-operation enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pinloop.config import SafetyConfig


class SafetyViolation(Exception):
    """Raised when a proposed change violates safety constraints."""


def check_budget_cap(daily_budget: float, config: SafetyConfig) -> None:
    """Reject if a proposed daily budget exceeds the configured maximum.

    Budget is in account currency units (e.g. dollars), NOT micro-currency.
    """
    if daily_budget > config.max_daily_budget:
        raise SafetyViolation(
            f"Daily budget {daily_budget:.2f} exceeds maximum "
            f"{config.max_daily_budget:.2f} (raise PINLOOP_MAX_DAILY_BUDGET to allow)."
        )


def check_blocked_operation(operation: str, config: SafetyConfig) -> None:
    """Reject if operation is in the blocked list."""
    if operation in config.blocked_operations:
        raise SafetyViolation(f"Operation '{operation}' is blocked by configuration")
