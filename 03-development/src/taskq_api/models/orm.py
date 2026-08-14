"""Task ORM definition.

[FR-01] — Task resource columns per SAD §3.1.

Citations:
- taskq_api.models.orm:Task  mirrors `tasks` table per SAD §3.1.1
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


class Task:
    """Lightweight task row representation.

    Per FR-01 the public response shape includes the columns the test
    inventory asserts on: id, command, name, status, created_at.

    Citations:
    - taskq_api.models.orm:Task.__init__  fields per SPEC §3 FR-01
    """

    def __init__(
        self,
        id: str,
        name: str,
        command: str,
        status: str = "pending",
        created_at: Optional[datetime] = None,
    ) -> None:
        # [FR-01]
        self.id = id
        self.name = name
        self.command = command
        self.status = status
        self.created_at = created_at or datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable view of the row."""
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


__all__: list[str] = ["Task"]
