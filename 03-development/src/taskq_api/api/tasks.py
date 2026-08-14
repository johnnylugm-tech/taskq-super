"""POST/GET/LIST/DELETE handlers for /v1/tasks.

[FR-01] — NFR-10 mandates integration via httpx ASGI transport (the
test exercises this exact code path).

Citations:
- taskq_api.api.tasks:create_task_endpoint  AC-1.1 / AC-1.2 / AC-1.3 / AC-1.7
- taskq_api.api.tasks:get_task_endpoint     AC-1.4 / AC-1.5
- taskq_api.api.tasks:list_tasks_endpoint   AC-1.8 / AC-1.9
- taskq_api.api.tasks:delete_task_endpoint  AC-1.6 / AC-1.10
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response, status

from taskq_api.api.deps import require_scope
from taskq_api.config import get_settings
from taskq_api.errors import problem
from taskq_api.models.schemas import TaskCreate
from taskq_api.service import tasks as tasks_service
from taskq_api.service.tasks import InvalidLimit

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task_endpoint(
    body: TaskCreate,
    _auth: dict = Depends(require_scope("write")),
) -> dict:
    """Create a new task.

    Citations:
    - taskq_api.api.tasks:create_task_endpoint  AC-1.1 / AC-1.2 / AC-1.3 / AC-1.7
    """
    # [FR-01]
    return tasks_service.create_task(name=body.name, command=body.command)


@router.get("")
async def list_tasks_endpoint(
    limit: Optional[int] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    _auth: dict = Depends(require_scope("read")),
) -> dict:
    """Cursor-paginated list of tasks.

    Citations:
    - taskq_api.api.tasks:list_tasks_endpoint  AC-1.8 / AC-1.9
    """
    # [FR-01]
    settings = get_settings()
    effective_limit = settings.default_list_limit if limit is None else limit

    try:
        page, next_cursor = tasks_service.list_tasks(
            limit=effective_limit,
            cursor=cursor,
            status=status_filter,
            min_limit=settings.min_list_limit,
            max_limit=settings.max_list_limit,
        )
    except InvalidLimit:
        raise problem(
            422,
            "Validation Error",
            f"limit must be in [{settings.min_list_limit}, {settings.max_list_limit}]",
        )

    return {
        "items": page,
        "limit": effective_limit,
        "cursor": cursor,
        "next_cursor": next_cursor,
    }


@router.get("/{task_id}")
async def get_task_endpoint(
    task_id: str,
    _auth: dict = Depends(require_scope("read")),
) -> dict:
    """Fetch a single task by id, or 404.

    Citations:
    - taskq_api.api.tasks:get_task_endpoint  AC-1.4 / AC-1.5
    """
    # [FR-01]
    return tasks_service.get_task(task_id)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task_endpoint(
    task_id: str,
    _auth: dict = Depends(require_scope("admin")),
) -> Response:
    """Delete a task (admin scope only).

    Admin authorization is enforced by the `require_scope("admin")` dep
    above; the dep raises 403 before any existence check, so the response
    cannot leak whether `task_id` exists (AC-1.6 / T-05).

    Citations:
    - taskq_api.api.tasks:delete_task_endpoint  AC-1.6 / AC-1.10
    """
    # [FR-01]
    tasks_service.delete_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__: list[str] = [
    "router",
    "create_task_endpoint",
    "get_task_endpoint",
    "list_tasks_endpoint",
    "delete_task_endpoint",
]
