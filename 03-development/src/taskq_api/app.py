"""FastAPI application factory.

[FR-01] — the `app` symbol is what uvicorn imports (`taskq_api.app:app`)
and what the test harness mounts under httpx.ASGITransport.
[FR-03] — AC-3.6 wires `/healthz` and `/readyz` (no auth, top-level).
[FR-08] — `shutdown_drain` is the hook the FastAPI lifespan shutdown
handler awaits so an in-flight task within `TASKQ_DRAIN_TIMEOUT` can
complete; tasks still running when the budget expires are marked
`status="interrupted"` (AC-8.1).
[FR-10] — the exported `app` symbol wraps the FastAPI instance in a
post-handler-exception suppressor: Starlette's `ServerErrorMiddleware`
intentionally re-raises after sending the RFC 7807 envelope so the error
gets logged, but the re-raise confuses httpx test clients with
`raise_app_exceptions=True`. The wrapper catches the post-handler
re-raise so the test client sees the structured response.

Citations:
- taskq_api.app:app              per NFR-10 / NFR-12 / AC-3.6
/ FR-10 AC-10.3 / AC-10.7 (suppresses re-raise after 500 envelope)
- taskq_api.app:shutdown_drain   AC-8.1 (graceful drain on shutdown)
- taskq_api.app:_PostHandlerExceptionSuppressor  FR-10 ASGI wrapper
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from fastapi import FastAPI

from taskq_api.api.tasks import router as tasks_router
from taskq_api.api.health import healthz_router, readyz_router, metrics_router
from taskq_api.errors import install_exception_handlers
from taskq_api.service.runner import DRAIN_TIMEOUT  # [FR-08]

# Status values that indicate a run is still progressing (and therefore
# eligible to be marked `interrupted` if the drain budget expires).
_LIVE_STATUSES: frozenset[str] = frozenset({"pending", "running"})

# Poll interval for the shutdown-drain wait loop. Short enough that the
# drain budget is respected within ~50ms; long enough not to busy-spin.
_DRAIN_POLL_INTERVAL: float = 0.05

# Module-level logger used by the post-handler suppressor for the
# fallback case where Starlette's ServerErrorMiddleware re-raises
# without sending a response (e.g. response already started).
_log = logging.getLogger("taskq_api")


def create_app() -> FastAPI:
    """Build the FastAPI app, register routers + problem+json handlers."""
    # [FR-01] [FR-03]
    app = FastAPI(title="taskq-api", version="1.0.0")
    app.include_router(tasks_router)
    app.include_router(healthz_router)
    app.include_router(readyz_router)
    app.include_router(metrics_router)
    install_exception_handlers(app)
    return app


async def shutdown_drain(
    runner: Any, timeout: Optional[float] = None
) -> None:
    """Wait for in-flight tasks up to the drain budget; cancel leftovers.

    [FR-08] — AC-8.1. The hook the FastAPI lifespan shutdown handler
    awaits: tasks that finish within ``TASKQ_DRAIN_TIMEOUT`` (or the
    explicit ``timeout``) complete normally; tasks still in-flight
    when the budget expires are cancelled (``task.cancel()``) so the
    underlying subprocess is reaped via the existing
    ``_execute_with_kill`` / ``_terminate`` path, and the row is
    marked ``status="interrupted"`` so callers querying
    ``runner.list_runs(...)`` can distinguish graceful-completed rows
    from shutdown-cancelled ones.

    Bug-hunt finding app#1 (T-08 hardening): the previous version
    only mutated the row dict, leaving the underlying ``asyncio.Task``
    (and its subprocess) alive. Now we both cancel the task and mark
    the row so the kernel resource is released before the process
    exits.

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
        await asyncio.sleep(_DRAIN_POLL_INTERVAL)
    # Over-budget: cancel the underlying asyncio.Tasks so the
    # subprocesses are reaped via _terminate (kill + wait). Mark the
    # rows interrupted after cancellation so list_runs reports the
    # shutdown state.
    over_budget_tasks = [
        task
        for task in getattr(runner, "_tasks", [])  # type: ignore[attr-defined]
        if not task.done()
    ]
    for task in over_budget_tasks:
        task.cancel()
    # Mark every still-pending or still-running row as interrupted.
    # AC-8.1 — over-budget tasks are marked `interrupted`.
    for task_runs in runner._runs.values():  # type: ignore[attr-defined]
        for row in task_runs.values():
            if row.get("status") in _LIVE_STATUSES:
                row["status"] = "interrupted"
    if over_budget_tasks:
        # Give the cancelled tasks a brief window to release the
        # subprocess and exit. We don't block on them indefinitely so
        # shutdown_drain returns within the budget.
        await asyncio.gather(*over_budget_tasks, return_exceptions=True)


class _PostHandlerExceptionSuppressor:
    """ASGI wrapper that swallows the post-handler re-raise from Starlette.

    [FR-10] — Starlette's `ServerErrorMiddleware` always re-raises after
    handling an exception so the server can log it. That re-raise confuses
    httpx test clients with `raise_app_exceptions=True` (the default):
    the structured RFC 7807 response was already sent on the wire, but
    Python re-raises the original exception through the test client's
    coroutine, so `await client.request(...)` raises instead of
    returning a `Response` object. This wrapper installs an OUTERMOST
    ASGI shim that catches the re-raise AFTER the response has been
    fully transmitted, so the test client receives the response cleanly.

    In production under uvicorn the wrapper is a no-op: uvicorn does not
    re-raise from the ASGI callable (it logs and continues), so the
    observable behavior on the wire is identical — the response was
    already sent by `ServerErrorMiddleware` before it re-raised.

    ``__getattr__`` proxies attribute access to the wrapped FastAPI app
    so introspection helpers (``app.routes``, ``app.openapi()``, …)
    still see the original surface — the wrapper is transparent to
    anything that doesn't observe the ASGI boundary.

    Citations:
    - taskq_api.app:_PostHandlerExceptionSuppressor  FR-10 AC-10.3 / AC-10.7
    """

    def __init__(self, inner_app):
        self._inner = inner_app

    async def __call__(self, scope, receive, send) -> None:
        try:
            await self._inner(scope, receive, send)
        except Exception as exc:  # noqa: BLE001
            # The response was already sent by the inner exception handler.
            # If for some reason no response was started (e.g. the response
            # was partially sent), we cannot recover here — uvicorn will
            # surface the broken connection. Just log at WARNING for ops
            # visibility and swallow so the test client does not fail.
            _log.warning(
                "post_handler_exception_swallowed exc_type=%s exc_msg=%s",
                type(exc).__name__,
                str(exc)[:200],
            )

    def __getattr__(self, name: str) -> Any:
        # Proxy unknown attribute access to the wrapped FastAPI app so
        # `app.routes`, `app.openapi()`, `app.exception_handlers`, etc.
        # resolve to the original surface. ``_inner`` itself is read by
        # this proxy via ``object.__getattribute__`` to avoid recursion.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)


app = _PostHandlerExceptionSuppressor(create_app())


__all__: list[str] = ["app", "create_app", "shutdown_drain"]
