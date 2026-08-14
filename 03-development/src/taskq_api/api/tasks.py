"""POST/GET/LIST/DELETE handlers for /v1/tasks.

[FR-01] — NFR-10 mandates integration via httpx ASGI transport (the
test exercises this exact code path).
[FR-02] — adds the run/lifecycle endpoints and the runs history endpoint.
[FR-04] — AC-4.1 / AC-4.2 / AC-4.3 / AC-4.4 / AC-4.5: every route below
attaches `Depends(require_scope(...))` (the single authz dep) so the
scope hierarchy `read < write < admin` is enforced consistently; admin
keys satisfy every endpoint.

Citations:
- taskq_api.api.tasks:create_task_endpoint  AC-1.1 / AC-1.2 / AC-1.3 / AC-1.7
/ AC-4.1 / AC-4.4 / AC-4.5
- taskq_api.api.tasks:get_task_endpoint     AC-1.4 / AC-1.5
/ AC-4.4 / AC-4.5
- taskq_api.api.tasks:list_tasks_endpoint   AC-1.8 / AC-1.9
/ AC-4.5
- taskq_api.api.tasks:delete_task_endpoint  AC-1.6 / AC-1.10
/ AC-4.2 / AC-4.4 / AC-4.5
- taskq_api.api.tasks:run_task_endpoint     AC-2.1 / AC-2.3 / AC-2.4
/ AC-4.3 / AC-4.4 / AC-4.5
- taskq_api.api.tasks:list_runs_endpoint    AC-2.6 / AC-4.5
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response, status

from taskq_api.api.deps import require_scope
from taskq_api.config import get_settings
from taskq_api.errors import problem
from taskq_api.models.schemas import TaskCreate
from taskq_api.service import tasks as tasks_service
from taskq_api.service import runner as runner_service
from taskq_api.service.tasks import InvalidLimit

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task_endpoint(
    body: TaskCreate,
    _auth: dict = Depends(require_scope("write")),
) -> dict:
    """Create a new task.

    [FR-04] — AC-4.1: a `read`-key attached to this endpoint is rejected
    with HTTP 403 + problem+json by `require_scope("write")` BEFORE the
    handler runs, so the response body never echoes the payload.

    Citations:
    - taskq_api.api.tasks:create_task_endpoint  AC-1.1 / AC-1.2 / AC-1.3 / AC-1.7
    / AC-4.1 / AC-4.4 / AC-4.5
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

    [FR-04] — AC-4.5: this `/v1` route attaches the single authz dep
    `Depends(require_scope("read"))`; the dep is the same factory every
    other /v1 route uses.

    Citations:
    - taskq_api.api.tasks:list_tasks_endpoint  AC-1.8 / AC-1.9 / AC-4.5
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

    [FR-04] — AC-4.4: an admin key on this endpoint passes the
    `require_scope("read")` gate (admin has `read` in its scopes set).

    Citations:
    - taskq_api.api.tasks:get_task_endpoint  AC-1.4 / AC-1.5 / AC-4.4 / AC-4.5
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

    [FR-04] — AC-4.2: a `write`-key attached to this endpoint is rejected
    with HTTP 403 + problem+json BEFORE any handler logic runs, so the
    body cannot say whether `task_id` exists (NFR-02 / T-05 / NP-08).

    Citations:
    - taskq_api.api.tasks:delete_task_endpoint  AC-1.6 / AC-1.10
    / AC-4.2 / AC-4.4 / AC-4.5
    """
    # [FR-01]
    tasks_service.delete_task(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# FR-02 — Run and runs-history endpoints
# ---------------------------------------------------------------------------


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_task_endpoint(
    task_id: str,
    _auth: dict = Depends(require_scope("write")),
) -> dict:
    """Schedule a task run; return HTTP 202 with a `run_id`.

    The subprocess is executed synchronously inside the handler so the
    lifecycle transition (`pending → running → done | failed | timeout`)
    is fully persisted before the HTTP response is finalised — that is
    what the polling test relies on (NFR-10 / AC-2.1 / AC-2.3).

    [FR-04] — AC-4.3: a `write`-key on this endpoint returns 202; a
    `read`-key returns 403 (the test creates the task with the write
    key, then exercises both branches through the same `require_scope(
    "write")` gate).

    Citations:
    - taskq_api.api.tasks:run_task_endpoint  AC-2.1 / AC-2.3 / AC-2.4 / AC-2.5
    / AC-4.3 / AC-4.4 / AC-4.5
    """
    # [FR-02]
    task = tasks_service.get_task(task_id)
    row = await runner_service.run_command(
        task_id,
        task["command"],
        timeout=get_settings().task_timeout,
    )
    return {"run_id": row["id"], "status": row["status"]}


@router.get("/{task_id}/runs")
async def list_runs_endpoint(
    task_id: str,
    _auth: dict = Depends(require_scope("read")),
) -> dict:
    """Return run history for the task, newest-first by `finished_at` desc.

    [FR-04] — AC-4.5: this `/v1` route attaches the single authz dep
    `Depends(require_scope("read"))`.

    Citations:
    - taskq_api.api.tasks:list_runs_endpoint  AC-2.6 / AC-4.5
    """
    # [FR-02]
    # 404 if the task itself doesn't exist (consistent with GET /v1/tasks/{id}).
    tasks_service.get_task(task_id)
    items = runner_service.list_runs(task_id)
    return {"items": items}


__all__: list[str] = [
    "router",
    "create_task_endpoint",
    "get_task_endpoint",
    "list_tasks_endpoint",
    "delete_task_endpoint",
    "run_task_endpoint",
    "list_runs_endpoint",
]
