"""Health endpoints — `/healthz` and `/readyz`.

[FR-01] placeholder for liveness.
[FR-03] — AC-3.6 — both `/healthz` and `/readyz` succeed without X-API-Key.
[FR-09] — liveness probe exemption.

Citations:
- taskq_api.api.health:healthz  per NFR-12 verifiability / AC-3.6
- taskq_api.api.health:readyz   per FR-09 / AC-3.6
"""

from __future__ import annotations

from fastapi import APIRouter

# [FR-03] AC-3.6 — both liveness and readiness are at top-level (no prefix).
healthz_router = APIRouter(tags=["health"])
readyz_router = APIRouter(tags=["health"])


@healthz_router.get("/healthz")
async def healthz() -> dict:
    """Return a tiny liveness document.

    Citations:
    - taskq_api.api.health:healthz  per NFR-12 verifiability / AC-3.6
    """
    # [FR-09] [FR-03]
    return {"status": "ok"}


@readyz_router.get("/readyz")
async def readyz() -> dict:
    """Return a tiny readiness document.

    Citations:
    - taskq_api.api.health:readyz  per FR-09 / AC-3.6
    """
    # [FR-09] [FR-03]
    return {"status": "ok"}


# Back-compat alias used by `app.include_router(...)` in app.py.
router = healthz_router

__all__: list[str] = ["router", "healthz_router", "readyz_router", "healthz", "readyz"]
