"""FastAPI dependency for X-API-Key auth + scope check.

[FR-01] — NFR-02 / NFR-04. Missing key -> 401; insufficient scope -> 403.
Neither branch leaks the key value in the response body (NFR-04).

Citations:
- taskq_api.api.deps:require_scope  AC-1.2 (no key -> 401), AC-1.6/AC-1.10 (scope gate)
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
    """

    def _dep(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
        # [FR-01]
        if not x_api_key:
            raise problem(401, "Unauthorized", "X-API-Key required")
        record = verify_key(x_api_key, scope)
        if record is None:
            # Same status for unknown-key and insufficient-scope so we
            # don't leak which one failed (NFR-02).
            raise problem(403, "Forbidden", "insufficient scope")
        return record

    return _dep


__all__: list[str] = ["require_scope"]
