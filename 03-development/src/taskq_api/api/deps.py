"""FastAPI dependency for X-API-Key auth + scope check.

[FR-01] — NFR-02 / NFR-04. Missing or invalid key -> 401.
[FR-03] — AC-3.1 (missing/invalid key -> 401 + problem+json),
AC-3.5 (revoked key -> 401). Insufficient scope still -> 403 (NFR-02).
[FR-04] — AC-4.1 / AC-4.2 (insufficient scope -> 403 + problem+json, no
resource-id leak). AC-4.5 (the single authz dependency every /v1 route
attaches via `require_scope(...)`).
[FR-05] — AC-5.1 / AC-5.4: the same dep also enforces the per-token
token-bucket rate limit; an over-budget call returns 429 + problem+json
with a `Retry-After` header. `/healthz` and `/readyz` are NOT routed
through this dep — they remain exempt (AC-5.3).

Citations:
- taskq_api.api.deps:require_scope  AC-1.2 (no key -> 401),
AC-1.6/AC-1.10 (scope gate), AC-3.1 / AC-3.5,
AC-4.1 / AC-4.2 / AC-4.5
- taskq_api.api.deps:_enforce_scope AC-5.1 (rate-limit gate) / AC-5.4
"""

from __future__ import annotations

from fastapi import Header

from taskq_api.errors import problem
from taskq_api.service.auth import INSUFFICIENT_SCOPE, verify_key
from taskq_api.service import ratelimit as ratelimit_service


def _enforce_scope(x_api_key: str | None, scope: str) -> dict:
    """Resolve an X-API-Key header into a key record with the required scope.

    Raises `problem(401, ...)` when the header is missing, the key is
    unknown, or the key has been revoked (AC-3.1 / AC-3.5). Raises
    `problem(403, ...)` when the key is known but lacks the required
    scope (FR-01 AC-1.6 / AC-1.10).

    [FR-04] — AC-4.1 / AC-4.2: a 403 response carries `detail` that does
    not echo the presented key or any task payload field, so the body
    cannot leak whether the addressed resource exists.
    [FR-05] — AC-5.1 / AC-5.4: after the scope check passes, the same
    token is fed to `check_rate_limit(<token>)`. On an over-budget call
    the dep raises `problem(429, ..., Retry-After=<int seconds>)`; the
    `Retry-After` header is set on the problem+json response by
    `errors.problem_json_response`.
    """
    if not x_api_key:
        raise problem(401, "Unauthorized", "X-API-Key required")
    record = verify_key(x_api_key, scope)
    if record is None:
        # [FR-03] AC-3.1 / AC-3.5 — missing key, unknown key, or revoked
        # key all return 401. Insufficient scope returns 403 below.
        raise problem(401, "Unauthorized", "invalid or revoked API key")
    if record.get(INSUFFICIENT_SCOPE):
        # [FR-01] AC-1.6 / AC-1.10 — known key without the required scope.
        raise problem(403, "Forbidden", "insufficient scope")
    # [FR-05] AC-5.1 / AC-5.4 — per-token token-bucket admission. Runs
    # after the scope check so the 429 path never leaks that the key
    # exists (a 401 would already have been raised above for unknown keys).
    admission = ratelimit_service.check_rate_limit(x_api_key)
    if admission.get("allow") is not True:
        retry_after = int(admission.get("retry_after", 1))
        # [FR-05] AC-5.2 — Retry-After is a positive integer (seconds).
        raise problem(
            429,
            "Too Many Requests",
            "rate limit exceeded",
            headers={"Retry-After": str(max(1, retry_after))},
        )
    return record


def require_scope(scope: str):
    """Build a FastAPI dependency that enforces `scope`.

    [FR-04] — AC-4.5: this is the single authz dependency. Every `/v1`
    route's `dependencies=` invokes `Depends(require_scope("read" |
    "write" | "admin"))`; the test
    `test_fr04_every_v1_route_declares_require_scope_dependency` walks
    `app.routes` and asserts every `/v1` route passes through this
    factory (`scoped_routes_count == routes_count`).
    """

    def _scope_dep(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict:
        return _enforce_scope(x_api_key, scope)

    return _scope_dep


__all__: list[str] = ["require_scope"]
