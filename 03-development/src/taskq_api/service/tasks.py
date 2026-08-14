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
from typing import Any, List, Optional, Tuple

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
        # `store` is typed as `Session` by the iterator annotation, but the
        # repository helpers expose a duck-typed surface (`insert`/`get`/
        # `list_paginated`/`delete`). The in-process test suite injects a
        # stand-in store that satisfies that surface; in production a thin
        # repository adapter translates the calls into ORM operations.
        # `Any` makes the duck typing explicit at the call site without
        # silently widening the type of unrelated Session users.
        store = cast_to_any(store)
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
        store = cast_to_any(store)
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
        store = cast_to_any(store)
        page, next_cursor = store.list_paginated(
            cursor=cursor, limit=limit, status=status
        )
    return page, next_cursor


def delete_task(task_id: str) -> None:
    """Delete a task.

    Authorization is the caller's responsibility: the HTTP layer enforces
    `admin` scope via `require_scope("admin")` (AC-1.6 / AC-1.10), which
    raises 403 before this function is reached, so the response cannot
    leak whether `task_id` exists (NFR-02 / T-05).

    Citations:
    - taskq_api.service.tasks:delete_task  AC-1.6 / AC-1.10
    """
    # [FR-01]
    with session_mod.transactional() as store:
        store = cast_to_any(store)
        deleted = store.delete(task_id)
    if not deleted:
        raise problem(404, "Not Found", "task does not exist")


def cast_to_any(value: object) -> Any:
    """Identity helper that returns its argument typed as ``Any``.

    The repository helpers expose a duck-typed surface (``insert`` /
    ``get`` / ``list_paginated`` / ``delete``); the production
    ``transactional()`` iterator is annotated as yielding a SQLAlchemy
    ``Session``. This helper widens the static type so the duck-typed
    method calls type-check, without changing the runtime value.
    """
    return value  # type: ignore[no-any-return]


__all__: list[str] = [
    "create_task",
    "get_task",
    "list_tasks",
    "delete_task",
    "InvalidLimit",
]
