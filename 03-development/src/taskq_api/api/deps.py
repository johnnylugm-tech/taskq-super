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

# Marker the service layer embeds in a verify result when the key is
# known but lacks the required scope; `require_scope` translates it
# into HTTP 403 (insufficient scope) rather than 401 (unknown key).
_INSUFFICIENT_SCOPE_MARKER: str = "_insufficient_scope"


def _enforce_scope(x_api_key: str | None, scope: str) -> dict:
    """Resolve an X-API-Key header into a key record with the required scope.

    Raises `problem(401, ...)` when the header is missing, the key is
    unknown, or the key has been revoked (AC-3.1 / AC-3.5). Raises
    `problem(403, ...)` when the key is known but lacks the required
    scope (FR-01 AC-1.6 / AC-1.10).
    """
    if not x_api_key:
        raise problem(401, "Unauthorized", "X-API-Key required")
    record = verify_key(x_api_key, scope)
    if record is None:
        # [FR-03] AC-3.1 / AC-3.5 — missing key, unknown key, or revoked
        # key all return 401. Insufficient scope returns 403 below.
        raise problem(401, "Unauthorized", "invalid or revoked API key")
    if record.get(_INSUFFICIENT_SCOPE_MARKER):
        # [FR-01] AC-1.6 / AC-1.10 — known key without the required scope.
        raise problem(403, "Forbidden", "insufficient scope")
    return record


def require_scope(scope: str) -> Callable[..., dict]:
    """Build a FastAPI dependency that enforces `scope`.

    Citations:
    - taskq_api.api.deps:require_scope  AC-1.2 / AC-1.6 / AC-1.10 / AC-1.4
    / AC-3.1 / AC-3.5
    """

    def _dep(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
        # [FR-01] [FR-03]
        return _enforce_scope(x_api_key, scope)

    return _dep


__all__: list[str] = ["require_scope"]
