"""FastAPI dependency for X-API-Key auth + scope check.

[FR-01] — NFR-02 / NFR-04. Missing or invalid key -> 401.
[FR-03] — AC-3.1 (missing/invalid key -> 401 + problem+json),
AC-3.5 (revoked key -> 401). Insufficient scope still -> 403 (NFR-02).

Citations:
- taskq_api.api.deps:require_scope  AC-1.2 (no key -> 401),
AC-1.6/AC-1.10 (scope gate), AC-3.1 / AC-3.5
"""

from __future__ import annotations

from typing import Callable

from fastapi import Header

from taskq_api.errors import problem
from taskq_api.service.auth import verify_key


def require_scope(scope: str) -> Callable:
    """Build a FastAPI dependency that enforces `scope`.

    Citations:
    - taskq_api.api.deps:require_scope  AC-1.2 / AC-1.6 / AC-1.10 / AC-1.4
    / AC-3.1 / AC-3.5
    """

    def _dep(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
        # [FR-01] [FR-03]
        if not x_api_key:
            raise problem(401, "Unauthorized", "X-API-Key required")
        record = verify_key(x_api_key, scope)
        if record is None:
            # [FR-03] AC-3.1 / AC-3.5 — missing key, unknown key, or revoked
            # key all return 401. Insufficient scope returns 403.
            raise problem(401, "Unauthorized", "invalid or revoked API key")
        if record.get("_insufficient_scope"):
            # [FR-01] AC-1.6 / AC-1.10 — known key without the required scope.
            raise problem(403, "Forbidden", "insufficient scope")
        return record

    return _dep


__all__: list[str] = ["require_scope"]
