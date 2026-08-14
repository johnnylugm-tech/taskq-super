"""Health endpoint.

[FR-01] placeholder for liveness.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/healthz", tags=["health"])


@router.get("")
async def healthz() -> dict:
    """Return a tiny liveness document.

    Citations:
    - taskq_api.api.health:healthz  per NFR-12 verifiability
    """
    # [FR-09]
    return {"status": "ok"}


__all__: list[str] = ["router", "healthz"]
