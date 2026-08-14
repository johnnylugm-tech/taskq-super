"""FastAPI application factory.

[FR-01] — the `app` symbol is what uvicorn imports (`taskq_api.app:app`)
and what the test harness mounts under httpx.ASGITransport.

Citations:
- taskq_api.app:app  per NFR-10 / NFR-12
"""

from __future__ import annotations

from fastapi import FastAPI

from taskq_api.api.tasks import router as tasks_router
from taskq_api.api.health import router as health_router
from taskq_api.errors import install_exception_handlers


def create_app() -> FastAPI:
    """Build the FastAPI app, register routers + problem+json handlers."""
    # [FR-01]
    app = FastAPI(title="taskq-api", version="1.0.0")
    app.include_router(tasks_router)
    app.include_router(health_router)
    install_exception_handlers(app)
    return app


app = create_app()


__all__: list[str] = ["app", "create_app"]
