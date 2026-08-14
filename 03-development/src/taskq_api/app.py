"""FastAPI application factory.

[FR-01] — the `app` symbol is what uvicorn imports (`taskq_api.app:app`)
and what the test harness mounts under httpx.ASGITransport.
[FR-03] — AC-3.6 wires `/healthz` and `/readyz` (no auth, top-level).
[FR-08] — `shutdown_drain` is the hook the FastAPI lifespan shutdown
handler awaits so an in-flight task within `TASKQ_DRAIN_TIMEOUT` can
complete; tasks still running when the budget expires are marked
`status="interrupted"` (AC-8.1).

Citations:
- taskq_api.app:app              per NFR-10 / NFR-12 / AC-3.6
- taskq_api.app:shutdown_drain   AC-8.1 (graceful drain on shutdown)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from fastapi import FastAPI

from taskq_api.api.tasks import router as tasks_router
from taskq_api.api.health import healthz_router, readyz_router
from taskq_api.errors import install_exception_handlers
from taskq_api.service.runner import DRAIN_TIMEOUT  # [FR-08]


def create_app() -> FastAPI:
    """Build the FastAPI app, register routers + problem+json handlers."""
    # [FR-01] [FR-03]
    app = FastAPI(title="taskq-api", version="1.0.0")
    app.include_router(tasks_router)
    app.include_router(healthz_router)
    app.include_router(readyz_router)
    install_exception_handlers(app)
    return app


async def shutdown_drain(
    runner: Any, timeout: Optional[float] = None
) -> None:
    """Wait for in-flight tasks up to the drain budget; mark leftovers `interrupted`.

    [FR-08] — AC-8.1. The hook the FastAPI lifespan shutdown handler
    awaits: tasks that finish within ``TASKQ_DRAIN_TIMEOUT`` (or the
    explicit ``timeout``) complete normally; tasks still in-flight
    when the budget expires are marked with ``status="interrupted"`` so
    callers querying ``runner.list_runs(...)`` can distinguish
    graceful-completed rows from shutdown-cancelled ones.

    Citations:
    - taskq_api.app:shutdown_drain  AC-8.1
    """
    budget = (
        timeout
        if timeout is not None
        else float(os.environ.get("TASKQ_DRAIN_TIMEOUT", str(DRAIN_TIMEOUT)))
    )
    deadline = time.monotonic() + budget
    # Wait for the runner's in-flight set to drain naturally.
    while runner.in_flight > 0:
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.05)
    # Mark every still-pending or still-running row as interrupted.
    # AC-8.1 — over-budget tasks are marked `interrupted`.
    for task_runs in runner._runs.values():  # type: ignore[attr-defined]
        for row in task_runs.values():
            if row.get("status") in ("pending", "running"):
                row["status"] = "interrupted"


app = create_app()


__all__: list[str] = ["app", "create_app", "shutdown_drain"]
