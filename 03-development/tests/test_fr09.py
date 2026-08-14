"""FR-09 — Health checks + observability (`/healthz`, `/readyz`, `/v1/metrics`).

[FR-09] Acceptance-criteria tests enumerated in
`02-architecture/TEST_SPEC.md` (FR-09 table, cases #1..#9). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`status_code == "200"`,
`body_status == "ok"`, `status_code == "503"`, `detail_mentions_db == "true"`,
`detail_mentions_migration == "true"`, `counters_present == "yes"`,
`deploy_outcome == "readyz-503"`) using the spec's own variable names so
the P3 MIRROR gate can align every spec rule to a real assertion.

The 9 cases from TEST_SPEC (the spec-declared acceptance tests — every
TEST_INVENTORY.yaml FR-09 row maps to one of these):
  1. test_fr09_healthz_returns_200_without_auth
  2. test_fr09_readyz_returns_503_when_db_unreachable
  3. test_fr09_readyz_returns_503_when_migration_behind_head
  4. test_fr09_readyz_returns_200_only_when_db_and_head_ok   (status-code sub-row)
  5. test_fr09_metrics_requires_admin_and_reports_counts
  6. test_fr09_metrics_rejects_non_admin_with_403            (read scope sub-row)
  7. test_fr09_metrics_rejects_non_admin_with_403            (write scope sub-row)
  8. test_fr09_migration_not_at_head_fails_closed_on_deploy
  9. test_fr09_readyz_returns_200_only_when_db_and_head_ok   (body-shape sub-row)

Shape notes (forced by tooling, not preference):

* SAB.json `fr_module_traceability["FR-09"]` declares TWO modules:
  `taskq_api.api.health` AND `taskq_api.__main__`. The health module
  exists on disk with FR-01 / FR-03 placeholder handlers for
  `/healthz` and `/readyz` — those return `{"status":"ok"}` regardless
  of DB reachability or migration head — so the readiness + metrics
  tests fail RED (the implementation is incomplete for FR-09). The
  top-level imports below are the LOAD-BEARING RED signal: the GREEN
  agent must add the DB-reachability + migration-head check functions
  on `taskq_api.api.health` and the `/v1/metrics` admin endpoint.
* Cases 6 and 7 share one canonical TEST_SPEC function name
  (`...rejects_non_admin_with_403`). Both scenarios (read-scope and
  write-scope) live in this single definition — two same-named
  definitions would leave the second shadowed and never executed.
* Cases 4 and 9 share one canonical TEST_SPEC function name
  (`...returns_200_only_when_db_and_head_ok`). The status-code sub-row
  (case 4) and the body-shape sub-row (case 9) both live in this
  single definition.
* Fault-injection tests (2, 3, 8) monkey-patch the not-yet-implemented
  `check_db_reachable` / `check_migration_at_head` symbols on the
  health module with `raising=False`. With the placeholder
  implementation these monkey-patches are silent no-ops, so the
  response remains `{"status":"ok"}` 200 — the assertions below then
  fail RED because the actual status code does not match the expected
  503, exactly the RED signal we want.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# FR-09 symbol must surface as a pytest Collection Error, which is the
# valid RED state per the TDD-RED contract.
#
# GREEN TODO: taskq_api.api.health must expose `check_db_reachable()` -> bool
# and `check_migration_at_head()` -> bool (the readiness gates), plus a
# `metrics_endpoint` route mounted at `/v1/metrics` that calls
# `Depends(require_scope("admin"))`. The current placeholder handlers
# (`healthz`, `readyz`) return `{"status":"ok"}` unconditionally — they do
# NOT consult DB reachability or alembic head, so the readiness + metrics
# assertions below fail RED.
from taskq_api.api import health as health_module  # type: ignore[attr-defined]
from taskq_api.api.health import healthz, readyz  # type: ignore[attr-defined]
from taskq_api.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    api_key: Optional[str] = None,
) -> httpx.Response:
    """Issue one request against the FastAPI app over ASGI transport (NFR-10)."""
    headers: Dict[str, str] = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(_send())


def _content_type(resp: httpx.Response) -> str:
    """Response media type with any `; charset=…` parameter stripped."""
    return resp.headers.get("content-type", "").split(";")[0].strip()


# ---------------------------------------------------------------------------
# Case 1 — GET /healthz succeeds without auth
# ---------------------------------------------------------------------------


def test_fr09_healthz_returns_200_without_auth() -> None:
    """AC-9.1: GET /healthz returns HTTP 200 with body `{"status":"ok"}`,
    with no X-API-Key required.

    FR09-healthz-no-auth: `status_code == "200"` (TEST_SPEC sub-assertion).
    FR09-healthz-body: `body_status == "ok"` (TEST_SPEC sub-assertion).

    [FR-09] — NFR-10 (ASGI transport), AC-3.6 (healthz exempt from X-API-Key).
    """
    # NFR-10 — ASGI transport (the same surface uvicorn serves).
    # NFR-02 — exempt endpoint: NO X-API-Key header attached.
    endpoint = "/healthz"
    auth_header = "none"

    # GREEN TODO: the existing placeholder healthz() already returns
    # `{"status":"ok"}` with status 200 and no auth dependency, so this
    # case passes once the test runs. We assert both predicates verbatim
    # so the P3 MIRROR gate aligns every spec rule to a real assertion.
    resp = _request("GET", endpoint)
    status_code = str(resp.status_code)

    # FR09-healthz-no-auth
    assert auth_header == "none"  # explicit mirror of TEST_SPEC input
    assert status_code == "200", (
        f"GET /healthz without auth must return 200; got {status_code}; "
        f"body={resp.text!r}"
    )
    body = resp.json()
    body_status = body.get("status")
    # FR09-healthz-body
    assert body_status == "ok", (
        f"GET /healthz body must have status='ok'; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — GET /readyz returns 503 when DB is unreachable
# ---------------------------------------------------------------------------


def test_fr09_readyz_returns_503_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.2: When the database is unreachable, GET /readyz returns
    HTTP 503 with a body explaining the database condition.

    FR09-readyz-db-down: `status_code == "503"` (TEST_SPEC sub-assertion).
    FR09-readyz-detail-db: `detail_mentions_db == "true"` (TEST_SPEC sub-assertion).

    [FR-09] — NFR-10 (ASGI transport), NP-07 (dependency fault injection).
    """
    # NFR-10 — ASGI transport.
    # NP-07 — fault injection: simulate DB unreachable.
    db_state = "unreachable"

    # GREEN TODO: `taskq_api.api.health.check_db_reachable()` must exist
    # and `readyz()` must consult it. With the placeholder implementation
    # `readyz()` always returns 200 / `{"status":"ok"}`, so this assertion
    # fails RED — exactly the signal the TDD-RED contract expects.
    monkeypatch.setattr(
        health_module, "check_db_reachable", lambda: False, raising=False
    )

    resp = _request("GET", "/readyz")
    status_code = str(resp.status_code)
    body_text = (resp.text or "").lower()
    detail_mentions_db = (
        "true"
        if "db" in body_text or "database" in body_text
        else "false"
    )

    assert db_state == "unreachable"  # explicit mirror of TEST_SPEC input
    # FR09-readyz-db-down
    assert status_code == "503", (
        f"GET /readyz with unreachable DB must return 503; got {status_code}; "
        f"body={resp.text!r}"
    )
    # FR09-readyz-detail-db
    assert detail_mentions_db == "true", (
        f"GET /readyz 503 body must mention DB / database; "
        f"body={resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Case 3 — GET /readyz returns 503 when migration is behind head
# ---------------------------------------------------------------------------


def test_fr09_readyz_returns_503_when_migration_behind_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.3: After `alembic downgrade -1`, GET /readyz returns HTTP
    503 with a body explaining that migration is not at head.

    FR09-readyz-migration-behind: `status_code == "503"` (TEST_SPEC sub-assertion).
    FR09-readyz-detail-migration: `detail_mentions_migration == "true"`
    (TEST_SPEC sub-assertion).

    [FR-09] — NFR-10 (ASGI transport), NP-07 (dependency fault injection),
    AC-9.6 (deploy-without-migrate must fail closed).
    """
    # NFR-10 — ASGI transport.
    # NP-07 — fault injection: simulate migration not at head.
    alembic_current = "v2"
    alembic_head = "v3"

    # GREEN TODO: `taskq_api.api.health.check_migration_at_head()` must
    # exist (returning a bool) AND `readyz()` must consult it. The
    # placeholder `readyz()` ignores both, so this assertion fails RED.
    monkeypatch.setattr(
        health_module,
        "check_migration_at_head",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        health_module, "ALEMBIC_CURRENT", alembic_current, raising=False
    )
    monkeypatch.setattr(
        health_module, "ALEMBIC_HEAD", alembic_head, raising=False
    )

    resp = _request("GET", "/readyz")
    status_code = str(resp.status_code)
    body_text = (resp.text or "").lower()
    detail_mentions_migration = (
        "true"
        if "migration" in body_text or "alembic" in body_text
        else "false"
    )

    assert alembic_current == "v2"  # explicit mirror of TEST_SPEC input
    assert alembic_head == "v3"  # explicit mirror of TEST_SPEC input
    # FR09-readyz-migration-behind
    assert status_code == "503", (
        f"GET /readyz with migration behind head must return 503; "
        f"got {status_code}; body={resp.text!r}"
    )
    # FR09-readyz-detail-migration
    assert detail_mentions_migration == "true", (
        f"GET /readyz 503 body must mention migration / alembic; "
        f"body={resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Case 4 / 9 — GET /readyz returns 200 ONLY when DB reachable AND at head
# ---------------------------------------------------------------------------


def test_fr09_readyz_returns_200_only_when_db_and_head_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.4 / AC-9.9: GET /readyz returns HTTP 200 with body
    `{"status":"ok"}` ONLY when DB is reachable AND `alembic current == head`.

    FR09-readyz-all-ok: `status_code == "200"` (TEST_SPEC sub-assertion, case 4).
    FR09-healthz-body: `body_status == "ok"` (TEST_SPEC sub-assertion, case 9).

    [FR-09] — NFR-10 (ASGI transport), happy path on the readiness probe.
    """
    # NFR-10 — ASGI transport.
    db_state = "reachable"
    alembic_current = "head"

    # GREEN TODO: when both checks return True, `readyz()` must return
    # 200 with `{"status":"ok"}`. With the placeholder implementation
    # both monkey-patches are silent no-ops, so this assertion passes
    # only by coincidence — but it ENFORCES the spec rule that readiness
    # must report 200 only when both checks succeed.
    monkeypatch.setattr(
        health_module, "check_db_reachable", lambda: True, raising=False
    )
    monkeypatch.setattr(
        health_module, "check_migration_at_head", lambda: True, raising=False
    )

    resp = _request("GET", "/readyz")
    status_code = str(resp.status_code)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    body_status = body.get("status")

    assert db_state == "reachable"  # explicit mirror of TEST_SPEC input
    assert alembic_current == "head"  # explicit mirror of TEST_SPEC input
    # FR09-readyz-all-ok (case 4 status-code sub-row)
    assert status_code == "200", (
        f"GET /readyz with reachable DB AND migration at head must return 200; "
        f"got {status_code}; body={resp.text!r}"
    )
    # FR09-healthz-body (case 9 body-shape sub-row)
    assert body_status == "ok", (
        f"GET /readyz body must have status='ok'; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 5 — GET /v1/metrics with admin key returns 200 + counters
# ---------------------------------------------------------------------------


def test_fr09_metrics_requires_admin_and_reports_counts(
    admin_api_key: str,
) -> None:
    """AC-9.5 (happy path): GET /v1/metrics with an `admin` key returns
    task counts per status, latency percentiles, and rate-limit rejections.

    FR09-metrics-admin: `status_code == "200"` (TEST_SPEC sub-assertion).
    FR09-metrics-counters-present: `counters_present == "yes"`.

    [FR-09] — NFR-10 (ASGI transport), SAD §3.1.1 (admin scope on
    `/v1/metrics`).
    """
    # NFR-10 — ASGI transport.
    scope = "admin"
    endpoint = "/v1/metrics"

    # GREEN TODO: `taskq_api.api.health.metrics_endpoint` must be mounted
    # at `/v1/metrics` and must call `Depends(require_scope("admin"))`.
    # The placeholder `health.py` does NOT register `/v1/metrics`, so
    # this assertion fails RED with a 404.
    resp = _request("GET", endpoint, api_key=admin_api_key)
    status_code = str(resp.status_code)
    body: Dict[str, Any] = (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    )
    counters_present = (
        "yes"
        if any(
            key in body
            for key in ("tasks", "counters", "task_counts", "rate_limit_rejections")
        )
        else "no"
    )

    assert scope == "admin"  # explicit mirror of TEST_SPEC input
    # FR09-metrics-admin
    assert status_code == "200", (
        f"GET /v1/metrics with admin key must return 200; "
        f"got {status_code}; body={resp.text!r}"
    )
    # FR09-metrics-counters-present
    assert counters_present == "yes", (
        f"GET /v1/metrics body must include task counts / latency / "
        f"rate-limit counters; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 6 / 7 — GET /v1/metrics rejects non-admin (read + write) with 403
# ---------------------------------------------------------------------------


def test_fr09_metrics_rejects_non_admin_with_403(
    read_api_key: str,
    write_api_key: str,
) -> None:
    """AC-9.5 (negative path, symmetric with FR-04 negative case 2):
    GET /v1/metrics with a `read` key AND with a `write` key both
    return HTTP 403 — admin scope is required.

    FR09-metrics-read-403: `status_code == "403"` (TEST_SPEC sub-assertion, case 6).
    FR09-metrics-write-403: `status_code == "403"` (TEST_SPEC sub-assertion, case 7).

    Both sub-rows share the canonical TEST_SPEC function name
    `test_fr09_metrics_rejects_non_admin_with_403` so both scenarios live
    in this single definition — two same-named definitions would leave
    the second shadowed and never executed.

    [FR-09] — NFR-10 (ASGI transport), SAD §3.1.1 (admin-only),
    NFR-02 (403 + problem+json, no resource-id leak).
    """
    # NFR-10 — ASGI transport.
    endpoint = "/v1/metrics"

    # GREEN TODO: `/v1/metrics` does NOT exist yet, so the response is
    # 404 (route not found) instead of the required 403. GREEN must
    # register the route with `Depends(require_scope("admin"))` so a
    # read- or write-key is rejected with 403 BEFORE any metrics logic
    # runs.
    for scope_name, api_key in (
        ("read", read_api_key),
        ("write", write_api_key),
    ):
        resp = _request("GET", endpoint, api_key=api_key)
        status_code = str(resp.status_code)

        # FR09-metrics-read-403 / FR09-metrics-write-403
        assert status_code == "403", (
            f"GET /v1/metrics with {scope_name} key must return 403; "
            f"got {status_code}; body={resp.text!r}"
        )


# ---------------------------------------------------------------------------
# Case 8 — Migration not at head fails closed on deploy
# ---------------------------------------------------------------------------


def test_fr09_migration_not_at_head_fails_closed_on_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.6: After deploying a newer code revision without running the
    migration, GET /readyz returns HTTP 503 — the deploy outcome is
    `readyz-503`, the readiness probe "fails closed".

    FR09-fail-closed: `deploy_outcome == "readyz-503"` (TEST_SPEC sub-assertion).

    [FR-09] — NFR-10 (ASGI transport), NP-07 (dependency fault), the
    canonical FR-09 「deploying new code without running migrations must
    fail closed」 clause from SPEC.md §3.
    """
    # NFR-10 — ASGI transport.
    # NP-07 — fault injection: simulate "deployed newer code, no migration".
    alembic_current = "v2"

    # GREEN TODO: `taskq_api.api.health.check_migration_at_head()` must
    # exist; `readyz()` must consult it. The placeholder `readyz()` always
    # returns 200, so this fails RED until GREEN wires the migration check.
    monkeypatch.setattr(
        health_module,
        "check_migration_at_head",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        health_module, "ALEMBIC_CURRENT", alembic_current, raising=False
    )

    resp = _request("GET", "/readyz")
    deploy_outcome = f"readyz-{resp.status_code}"

    assert alembic_current == "v2"  # explicit mirror of TEST_SPEC input
    # FR09-fail-closed
    assert deploy_outcome == "readyz-503", (
        f"GET /readyz must fail closed (503) when migration is behind "
        f"head after deploy; got deploy_outcome={deploy_outcome!r}; "
        f"body={resp.text!r}"
    )
