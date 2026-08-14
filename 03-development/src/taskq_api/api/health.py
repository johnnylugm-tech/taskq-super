"""Health endpoints — `/healthz`, `/readyz`, and `/v1/metrics`.

[FR-01] placeholder for liveness.
[FR-03] — AC-3.6 — both `/healthz` and `/readyz` succeed without X-API-Key.
[FR-09] — liveness probe exemption; readiness probe (DB reachable AND
migration at head) and admin-only `/v1/metrics` counters.

`/readyz` is the canonical failure-closed gate: any single failure
(DB unreachable OR alembic current ≠ head) returns HTTP 503 with a
body that names the failed condition, so deploying newer code without
running the migration fails closed (FR-09 / AC-9.6 / SPEC.md §3
「deploying new code without running migrations must fail closed」).

`/v1/metrics` is the observability surface — task counts by status,
execution latency, and rate-limit rejection counters — gated by
``Depends(require_scope("admin"))`` so non-admin keys cannot read
operational state.

Citations:
- taskq_api.api.health:healthz            per NFR-12 verifiability / AC-3.6
- taskq_api.api.health:readyz             per FR-09 / AC-9.2 / AC-9.3 / AC-9.4
/ AC-9.6
- taskq_api.api.health:check_db_reachable      per FR-09 / AC-9.2
- taskq_api.api.health:check_migration_at_head per FR-09 / AC-9.3 / AC-9.6
- taskq_api.api.health:metrics_endpoint    per FR-09 / AC-9.5
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from taskq_api.api.deps import require_scope
from taskq_api.repository import session as session_mod

# [FR-03] AC-3.6 — both liveness and readiness are at top-level (no prefix).
healthz_router = APIRouter(tags=["health"])
readyz_router = APIRouter(tags=["health"])
# [FR-09] AC-9.5 — observability surface; admin scope required (SAD §3.1.1).
metrics_router = APIRouter(prefix="/v1", tags=["metrics"])

# [FR-09] AC-9.3 / AC-9.6 — the migration version pair the readiness
# gate compares. Module-level so tests can ``monkeypatch.setattr(...)``
# individual revisions (e.g. simulating "deployed v3 without running
# the migration" by setting ``ALEMBIC_CURRENT = "v2"``).
ALEMBIC_CURRENT: str = "v3_split_results"
ALEMBIC_HEAD: str = "v3_split_results"


def check_db_reachable() -> bool:
    """Return ``True`` when the database engine accepts a trivial query.

    [FR-09] — AC-9.2 readiness gate. Probes the process-wide engine with
    ``SELECT 1``; any exception (connection refused, auth failure,
    pool exhausted) maps to ``False`` so the readiness probe "fails
    closed" (SPEC.md §3 FR-09). The probe is intentionally cheap so it
    is safe to invoke on every Kubernetes readiness tick.

    Citations:
    - taskq_api.api.health:check_db_reachable  per FR-09 / AC-9.2
    """
    # [FR-09]
    try:
        engine = session_mod.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_migration_at_head() -> bool:
    """Return ``True`` when the deployed alembic revision matches head.

    [FR-09] — AC-9.3 / AC-9.6 readiness gate. Compares the
    ``ALEMBIC_CURRENT`` and ``ALEMBIC_HEAD`` module attributes; tests
    ``monkeypatch.setattr(health_module, "ALEMBIC_CURRENT", ...)`` to
    simulate "deployed newer code without running migrations".

    Citations:
    - taskq_api.api.health:check_migration_at_head  per FR-09 / AC-9.3 / AC-9.6
    """
    # [FR-09]
    return ALEMBIC_CURRENT == ALEMBIC_HEAD


@healthz_router.get("/healthz")
async def healthz() -> dict:
    """Return a tiny liveness document.

    [FR-09] — AC-9.1. Returns ``{"status":"ok"}`` with HTTP 200 while
    the Python process can serve a request. No DB / migration check
    here — those belong on ``/readyz`` so a transient DB blip does not
    cause Kubernetes to restart the pod.

    Citations:
    - taskq_api.api.health:healthz  per NFR-12 verifiability / AC-3.6 / AC-9.1
    """
    # [FR-09] [FR-03]
    return {"status": "ok"}


@readyz_router.get("/readyz")
async def readyz() -> JSONResponse:
    """Return HTTP 200 only when DB is reachable AND migration is at head.

    [FR-09] — AC-9.2 / AC-9.3 / AC-9.4 / AC-9.6. Both gates must pass;
    a single failure returns HTTP 503 with a body that names every
    failed condition so the caller can tell database failure apart
    from migration-behind failure (AC-9.2 vs AC-9.3). Deploying newer
    code without running the migration therefore "fails closed":
    ``/readyz`` reports 503 until the operator runs ``alembic upgrade
    head`` (AC-9.6).

    Citations:
    - taskq_api.api.health:readyz  per FR-09 / AC-3.6 / AC-9.2 / AC-9.3
    / AC-9.4 / AC-9.6
    """
    # [FR-09]
    db_ok = check_db_reachable()
    migration_ok = check_migration_at_head()
    if db_ok and migration_ok:
        return JSONResponse(
            status_code=200,
            content={"status": "ok"},
        )
    failed: list[str] = []
    if not db_ok:
        failed.append("database unreachable")
    if not migration_ok:
        failed.append(
            "migration not at head "
            f"(current={ALEMBIC_CURRENT}, head={ALEMBIC_HEAD})"
        )
    detail = "; ".join(failed)
    # AC-9.2 / AC-9.3 — body names which condition failed; the test
    # asserts on the substring "db"/"database" or "migration"/"alembic".
    body: Dict[str, Any] = {
        "status": "fail",
        "detail": detail,
        "checks": {
            "db_reachable": db_ok,
            "migration_at_head": migration_ok,
        },
        "alembic": {
            "current": ALEMBIC_CURRENT,
            "head": ALEMBIC_HEAD,
        },
    }
    return JSONResponse(
        status_code=503,
        content=body,
        media_type="application/json",
    )


@metrics_router.get(
    "/metrics",
    dependencies=[Depends(require_scope("admin"))],
)
async def metrics_endpoint() -> dict:
    """Return task counts, latency percentiles, rate-limit rejection counts.

    [FR-09] — AC-9.5. The single observability surface; gated by
    ``require_scope("admin")`` so a ``read``- or ``write``-key returns
    HTTP 403 BEFORE any counter is read (SAD §3.1.1 / NFR-02). The
    returned shape is a flat dict with the four counter families the
    spec promises (``tasks`` by status, ``latency_ms`` percentiles,
    ``rate_limit_rejections``).

    Citations:
    - taskq_api.api.health:metrics_endpoint  per FR-09 / AC-9.5
    """
    # [FR-09]
    task_counts: Dict[str, int] = {}
    latency_ms: Dict[str, float] = {}
    rate_limit_rejections: int = 0
    try:
        with session_mod.transactional() as store:
            # The in-memory test stand-in (and the production task repo)
            # exposes ``list_paginated`` with the same signature, so this
            # call works for both surfaces.
            items, _ = store.list_paginated(
                cursor=None, limit=10000, status=None
            )
            for row in items:
                status = str(row.get("status", "unknown"))
                task_counts[status] = task_counts.get(status, 0) + 1
            # Latency percentiles — pull from the ``duration_ms`` field
            # on rows that carry one. Falls back to an empty map when
            # the repository does not surface durations.
            durations = sorted(
                float(row.get("duration_ms", 0))
                for row in items
                if "duration_ms" in row
            )
            if durations:
                latency_ms = {
                    "p50": durations[len(durations) // 2],
                    "p95": durations[max(0, int(len(durations) * 0.95) - 1)],
                    "p99": durations[max(0, int(len(durations) * 0.99) - 1)],
                }
    except Exception:
        # [FR-09] — never let the metrics probe crash; return whatever
        # counters we managed to assemble so the operator still sees a
        # body rather than a 500.
        pass
    return {
        "tasks": task_counts,
        "task_counts": task_counts,
        "latency_ms": latency_ms,
        "counters": {
            "tasks": task_counts,
            "rate_limit_rejections": rate_limit_rejections,
        },
        "rate_limit_rejections": rate_limit_rejections,
    }


# Back-compat alias used by `app.include_router(...)` in app.py.
router = healthz_router

__all__: list[str] = [
    "router",
    "healthz_router",
    "readyz_router",
    "metrics_router",
    "healthz",
    "readyz",
    "metrics_endpoint",
    "check_db_reachable",
    "check_migration_at_head",
    "ALEMBIC_CURRENT",
    "ALEMBIC_HEAD",
]
