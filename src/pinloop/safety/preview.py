"""Change preview formatting — structured output for proposed mutations.

Every write tool returns a ChangePlan preview and stores the plan in-process.
Nothing mutates Pinterest until confirm_and_apply is called with the plan_id.
Plans are in-memory only, so they expire when the server restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ChangePlan:
    """A proposed change that must be confirmed before execution."""

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    operation: str = ""
    entity_type: str = ""
    entity_id: str = ""
    account_id: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    requires_double_confirm: bool = False

    def to_preview(self) -> dict[str, Any]:
        """Format as a human-readable preview dict for the AI to present."""
        return {
            "plan_id": self.plan_id,
            "operation": self.operation,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "account_id": self.account_id,
            "changes": self.changes,
            "requires_double_confirm": self.requires_double_confirm,
            "status": "PENDING_CONFIRMATION",
            "instructions": (
                "Review the changes above. To apply, call confirm_and_apply "
                f"with plan_id='{self.plan_id}' and dry_run=false."
            ),
        }


_pending_plans: dict[str, ChangePlan] = {}


def store_plan(plan: ChangePlan) -> None:
    """Store a plan for later retrieval by confirm_and_apply."""
    _pending_plans[plan.plan_id] = plan


def get_plan(plan_id: str) -> ChangePlan | None:
    """Retrieve a stored plan by ID."""
    return _pending_plans.get(plan_id)


def remove_plan(plan_id: str) -> None:
    """Remove a plan after execution."""
    _pending_plans.pop(plan_id, None)
