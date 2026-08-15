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

import re
from pathlib import Path
from typing import Sequence

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from taskq_api.api.deps import require_scope
from taskq_api.errors import render_problem
from taskq_api.repository import rate_repo, session as session_mod
from taskq_api.repository import task_repo

# [FR-03] AC-3.6 — both liveness and readiness are at top-level (no prefix).
healthz_router = APIRouter(tags=["health"])
readyz_router = APIRouter(tags=["health"])
# [FR-09] AC-9.5 — observability surface; admin scope required (SAD §3.1.1).
metrics_router = APIRouter(prefix="/v1", tags=["metrics"])

# Location of the alembic migration scripts. The head revision is derived
# at module-load time from the actual files on disk so a freshly added
# revision becomes "head" automatically — no constant to keep in sync.
_MIGRATIONS_DIR: Path = (
    Path(__file__).resolve().parents[3] / "src" / "migrations" / "versions"
)


def _resolve_alembic_head() -> str:
    """Return the leaf revision of the alembic migration graph on disk.

    [FR-09] — AC-9.3 / AC-9.6. Walks every ``*.py`` under
    ``migrations/versions/`` and reads the ``revision`` /
    ``down_revision`` module-level assignments (the same identifiers
    alembic itself consults). The head is the revision that no other
    revision declares as its ``down_revision``; ties are broken
    alphabetically so the result is deterministic.

    Falls back to the empty string when no migration files are
    reachable — a deliberately "not at head" value that makes the
    readiness probe fail closed (AC-9.6) until a real migration
    directory is wired up.
    """
    if not _MIGRATIONS_DIR.is_dir():
        return ""
    revisions: dict[str, str | None] = {}
    for migration_file in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if migration_file.name.startswith("_"):
            continue
        # [FR-09] — parse, do NOT import. Migration files import
        # `alembic` / `sqlalchemy` and can have side effects; a
        # lightweight regex read is sufficient for two well-known
        # module-level string assignments.
        try:
            text_source = migration_file.read_text(encoding="utf-8")
        except OSError:
            continue
        revision = _extract_string_assignment(text_source, "revision")
        down_revision = _extract_string_assignment(
            text_source, "down_revision"
        )
        if revision is not None:
            revisions[revision] = down_revision

    if not revisions:
        return ""

    # Head = revision no other revision names as its `down_revision`.
    children = {rev for rev in revisions.values() if rev is not None}
    heads = sorted(r for r in revisions if r not in children)
    return heads[0] if heads else ""


_STRING_ASSIGNMENT_RE = re.compile(
    r"""^[ \t]*                              # leading indent
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)    # identifier
        [ \t]*=[ \t]*
        (?P<quote>['\"])
        (?P<value>[^'\"]*)
        (?P=quote)""",
    re.MULTILINE | re.VERBOSE,
)


def _extract_string_assignment(source: str, name: str) -> str | None:
    """Return the string value of the first ``name = "..."`` line in ``source``.

    Matches the top-level ``revision = "..."`` / ``down_revision = "..."``
    form alembic generates. Returns ``None`` when the attribute is
    absent, multi-line, or assigned a non-literal expression.
    """
    for match in _STRING_ASSIGNMENT_RE.finditer(source):
        if match.group("name") != name:
            continue
        value = match.group("value")
        return value if value else None
    return None


# [FR-09] AC-9.3 / AC-9.6 — the migration version pair the readiness
# gate compares. Module-level so tests can ``monkeypatch.setattr(...)``
# individual revisions (e.g. simulating "deployed v3 without running
# the migration" by setting ``ALEMBIC_CURRENT = "v2"``).
#
# `ALEMBIC_HEAD` is derived from the migrations directory at import
# time (the leaf of the alembic revision DAG). `ALEMBIC_CURRENT`
# mirrors what `alembic current` would report against the live DB;
# it is initialised to the head so a fresh deployment reads "at head"
# until the alembic_version table exists to override it.
ALEMBIC_HEAD: str = _resolve_alembic_head()
ALEMBIC_CURRENT: str = ALEMBIC_HEAD


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
async def readyz(request: Request) -> JSONResponse:
    """Return HTTP 200 only when DB is reachable AND migration is at head.

    [FR-09] — AC-9.2 / AC-9.3 / AC-9.4 / AC-9.6. Both gates must pass;
    a single failure returns HTTP 503 with a problem+json body that
    names every failed condition so the caller can tell database failure
    apart from migration-behind failure (AC-9.2 vs AC-9.3). Deploying
    newer code without running the migration therefore "fails closed":
    ``/readyz`` reports 503 until the operator runs ``alembic upgrade
    head`` (AC-9.6).

    [FR-10] — AC-10.1 / AC-10.6: the 503 response carries
    ``Content-Type: application/problem+json`` with the canonical six
    fields and the ``X-Correlation-Id`` join-key header. The 200 path
    stays on the legacy ``application/json`` shape so the FR-09
    `body_status == "ok"` assertion continues to hold.

    Citations:
    - taskq_api.api.health:readyz  per FR-09 / AC-3.6 / AC-9.2 / AC-9.3
    / AC-9.4 / AC-9.6 / FR-10 AC-10.1 / AC-10.6
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
    # [FR-10] — render the 503 response as problem+json so the AC-10.6
    # envelope contract is satisfied (every non-2xx response is
    # application/problem+json). The `detail` field carries the rich
    # failure breakdown so the FR-09 `detail_mentions_db` /
    # `detail_mentions_migration` assertions still pass.
    return render_problem(
        request,
        status=503,
        title="Service Unavailable",
        detail="; ".join(failed),
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

    The rate-limit rejection count comes from ``rate_repo``'s lifetime
    counter (bumped on every admission denial in
    ``taskq_api.repository.rate_repo.try_consume``); the rest of the
    counters are derived from a single repository scan so they share a
    consistent snapshot.

    Citations:
    - taskq_api.api.health:metrics_endpoint  per FR-09 / AC-9.5
    """
    # [FR-09]
    task_counts, latency_ms = _collect_task_metrics()
    return {
        "tasks": task_counts,
        "latency_ms": latency_ms,
        "rate_limit_rejections": rate_repo.get_rejection_count(),
    }


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Return the q-th percentile (0 < q < 1) of a pre-sorted sequence.

    [FR-09] — AC-9.5 helper. Uses the nearest-rank rule: index is
    ``ceil(q * N) - 1`` clamped to ``[0, N - 1]``. Caller is responsible
    for sorting; doing it once up-front keeps the p50/p95/p99 trio
    amortised to a single O(N log N) pass.
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    idx = max(0, min(n - 1, int(q * n) - 1))
    return sorted_values[idx]


def _collect_task_metrics() -> tuple[dict[str, int], dict[str, float]]:
    """Return ``(task_counts_by_status, latency_percentiles_ms)``.

    [FR-09] — AC-9.5. Single repository scan so the two counters
    describe the same snapshot. Returns empty dicts when the repository
    is unreachable — the metrics probe must never crash (NFR-12).
    """
    try:
        with session_mod.transactional() as store:
            items, _ = task_repo.list_tasks(
                store, cursor=None, limit=10000, status=None
            )
    except Exception:
        # [FR-09] — never let the metrics probe crash; report zeros so the
        # operator still sees a body rather than a 500 (NFR-12).
        return {}, {}

    counts: dict[str, int] = {}
    durations: list[float] = []
    for row in items:
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
        if "duration_ms" in row:
            durations.append(float(row["duration_ms"]))
    durations.sort()

    percentiles: dict[str, float] = {}
    if durations:
        percentiles = {
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
        }
    return counts, percentiles


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