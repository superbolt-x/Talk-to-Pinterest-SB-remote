"""Mutation audit logging — every write operation is logged locally."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_mutation(
    log_file: str,
    *,
    operation: str,
    account_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    changes: dict[str, Any] | None = None,
    dry_run: bool = True,
    result: str = "success",
    error: str = "",
) -> None:
    """Append a mutation record to the audit log file."""
    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "account_id": account_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "changes": changes or {},
        "dry_run": dry_run,
        "result": result,
        "error": error,
    }

    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
