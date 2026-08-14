"""Task service — business logic for FR-01 CRUD.

[FR-01] — orchestrates repository calls, enforces uniqueness, raises
structured errors that the API layer maps to HTTP problem+json.

Citations:
- taskq_api.service.tasks:create_task   AC-1.1 / AC-1.7
- taskq_api.service.tasks:get_task      AC-1.4 / AC-1.5
- taskq_api.service.tasks:list_tasks    AC-1.8 / AC-1.9
- taskq_api.service.tasks:delete_task   AC-1.6 / AC-1.10
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from taskq_api.errors import problem
from taskq_api.repository import session as session_mod


class InvalidLimit(Exception):
    """Raised when `limit` is outside [min, max]."""


def create_task(
    name: str,
    command: str,
    status: str = "pending",
) -> dict:
    """Persist a new task and return the row dict.

    Citations:
    - taskq_api.service.tasks:create_task  AC-1.1 / AC-1.7 (duplicate -> 409)
    """
    # [FR-01]
    row = {
        "id": str(uuid.uuid4()),
        "name": name,
        "command": command,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with session_mod.transactional() as store:
        try:
            store.insert(row)
        except KeyError as exc:
            if exc.args and exc.args[0] == "duplicate_name":
                raise problem(409, "Conflict", "task with this name already exists")
            raise
    return row


def get_task(task_id: str) -> dict:
    """Fetch a single task by id or raise a 404 problem.

    Citations:
    - taskq_api.service.tasks:get_task  AC-1.4 / AC-1.5
    """
    # [FR-01]
    with session_mod.transactional() as store:
        row = store.get(task_id)
    if row is None:
        raise problem(404, "Not Found", "task does not exist")
    return row


def list_tasks(
    limit: int,
    cursor: Optional[str] = None,
    status: Optional[str] = None,
    min_limit: int = 1,
    max_limit: int = 200,
) -> Tuple[List[dict], Optional[str]]:
    """List tasks with cursor-based pagination.

    Citations:
    - taskq_api.service.tasks:list_tasks  AC-1.8 / AC-1.9
    """
    # [FR-01]
    if limit < min_limit or limit > max_limit:
        raise InvalidLimit(f"limit must be in [{min_limit}, {max_limit}]")
    with session_mod.transactional() as store:
        page, next_cursor = store.list_paginated(
            cursor=cursor, limit=limit, status=status
        )
    return page, next_cursor


def delete_task(task_id: str, *, is_admin: bool) -> None:
    """Delete a task; admin scope only.

    The non-admin branch is intentionally eager: a 403 is returned BEFORE
    the existence check so the response never reveals whether `task_id`
    exists (NFR-02 / T-05).

    Citations:
    - taskq_api.service.tasks:delete_task  AC-1.6 / AC-1.10
    """
    # [FR-01]
    if not is_admin:
        # No existence lookup -> no id-leak.
        raise problem(403, "Forbidden", "admin scope required to delete")
    with session_mod.transactional() as store:
        deleted = store.delete(task_id)
    if not deleted:
        raise problem(404, "Not Found", "task does not exist")


__all__: list[str] = [
    "create_task",
    "get_task",
    "list_tasks",
    "delete_task",
    "InvalidLimit",
]
