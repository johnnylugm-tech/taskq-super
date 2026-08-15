"""Task CRUD repository helpers.

[FR-01] — repository-side helpers that operate on the store opened by
`transactional()`. In production these would translate to SQLAlchemy Core
calls against the `tasks` table; for GREEN they are reference shapes
that the in-memory test store already satisfies.

Citations:
- taskq_api.repository.task_repo:insert_task  AC-1.1
- taskq_api.repository.task_repo:get_task     AC-1.4 / AC-1.5
- taskq_api.repository.task_repo:list_tasks   AC-1.8 / AC-1.9
- taskq_api.repository.task_repo:delete_task  AC-1.6 / AC-1.10
"""

from __future__ import annotations

# pragma: no error-handling — pure pass-through to the injected store.
# Store failures (e.g. KeyError('duplicate_name')) are part of the
# published contract and must reach the caller unmodified.

from typing import List, Optional


def insert_task(store, row: dict) -> None:
    """Insert a new task row. Raises KeyError('duplicate_name') on conflict."""
    # [FR-01]
    store.insert(row)


def get_task(store, task_id: str) -> Optional[dict]:
    """Fetch a single task by id, or None if absent."""
    # [FR-01]
    return store.get(task_id)


def delete_task(store, task_id: str) -> bool:
    """Delete a task by id. Returns True iff a row was removed."""
    # [FR-01]
    return store.delete(task_id)


def list_tasks(
    store,
    cursor: Optional[str],
    limit: int,
    status: Optional[str],
) -> "tuple[List[dict], Optional[str]]":
    """Cursor-paginated list of tasks.

    Citations:
    - taskq_api.repository.task_repo:list_tasks  AC-1.9 (no offset, cursor only)
    """
    return store.list_paginated(cursor=cursor, limit=limit, status=status)


__all__: list[str] = ["insert_task", "get_task", "delete_task", "list_tasks"]
