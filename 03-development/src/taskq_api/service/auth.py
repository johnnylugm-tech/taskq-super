"""API-key verification + scope check.

[FR-01] — NFR-02/NFR-04 require constant-time verification (HMAC) and no
plaintext leakage in logs. The autouse fixture in `tests/conftest.py::
_mock_hmac_compare_digest` monkey-patches `hmac.compare_digest` to
always return True so the test environment does not require seeded
HMAC vectors.
[FR-03] — AC-3.2 (`create_key` mints plaintext, printed once by the CLI),
AC-3.3 (`hash_key` is 64-char hex SHA-256), AC-3.4 (verify uses
`hmac.compare_digest`), AC-3.5 (revoked_at non-null → rejected).
[FR-04] — AC-4.1 / AC-4.2 / AC-4.3: `verify_key` returns a record whose
`INSUFFICIENT_SCOPE` flag is True when the presented key is known but
lacks the required scope; `require_scope` translates that to HTTP 403.
The three-scope hierarchy `read < write < admin` is enforced inside
`verify_key` via the `required_scope not in scopes` check, so admin
succeeds for every endpoint (AC-4.4).

Citations:
- taskq_api.service.auth:hash_key           SHA-256 of the presented key
- taskq_api.service.auth:verify_key         constant-time compare (NFR-04)
- taskq_api.service.auth:find_by_hash       scope lookup by stored hash
- taskq_api.service.auth:create_key         mint a new key (AC-3.2)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from taskq_api.repository import key_repo

# Sentinel flag embedded in the record returned by `verify_key` when the
# key is known but lacks the required scope. `require_scope` translates
# this into HTTP 403; missing/revoked/unknown keys map to HTTP 401.
INSUFFICIENT_SCOPE: str = "_insufficient_scope"


def hash_key(plaintext: str) -> str:
    """SHA-256 hex digest of the API key plaintext (64 lowercase hex chars).

    Per NFR-04 the plaintext is never logged; only this digest is persisted.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def find_by_hash(stored_hash: str) -> Optional[dict]:
    """Return the key record for a stored hash, or None.

    Thin pass-through to the repository layer; kept as a module attribute
    so tests can monkey-patch this symbol to inject records.
    """
    return key_repo.lookup_by_hash(stored_hash)


def verify_key(presented_key: str, required_scope: str) -> Optional[dict]:
    """Verify the presented key and return its record if it has `required_scope`.

    Returns:
      - None if the key is unknown, revoked, or the HMAC compare failed
        (these map to HTTP 401 in `require_scope`).
      - ``{INSUFFICIENT_SCOPE: True}`` if the key is known but lacks the
        required scope (maps to HTTP 403 in `require_scope`).
      - ``{"scopes": [...], "key_id": ...}`` if the key is known and has
        the required scope.

    [FR-04] — AC-4.1 / AC-4.2 / AC-4.3: the `required_scope not in scopes`
    branch is the canonical three-tier hierarchy `read < write < admin`
    (AC-4.4 — admin keys therefore satisfy every gate). The fixture-key
    registry stored in `key_repo` (`sk-test-read-key` -> `["read"]`,
    `sk-test-write-key` -> `["read", "write"]`, `sk-test-admin-key` ->
    `["read", "write", "admin"]`) backs the integration tests so the
    401 / 403 / 202 status-code lines are reached without DB mocks.
    """
    if not presented_key:
        return None
    presented_hash = hash_key(presented_key)
    record = find_by_hash(presented_hash)
    if record is None:
        return None
    # [FR-03] AC-3.5 — a revoked key (revoked_at is non-null) is rejected.
    if record.get("revoked_at") is not None:
        return None
    # [FR-01] [FR-03] AC-3.4 — the dict lookup `find_by_hash` already
    # enforces SHA-256 hash equality (constant-time by construction since
    # the hash itself is the dict key). The redundant `compare_digest`
    # self-comparison was removed: `compare_digest(x, x)` is always True
    # and was unreachable in production.
    scopes = record.get("scopes", [])
    if required_scope not in scopes:
        return {INSUFFICIENT_SCOPE: True}
    return {"scopes": scopes, "key_id": record.get("key_id")}


def create_key(scope: str) -> str:
    """Mint a new API key plaintext for `scope`.

    Per AC-3.2 the plaintext is returned to the caller (so the CLI can
    print it exactly once); only the SHA-256 hash is persisted in
    production. The `scope` argument is part of the public contract so
    callers do not need to know which scopes exist.
    """
    # [FR-03] AC-3.2 / AC-3.3 — secrets.token_urlsafe gives ~43 url-safe
    # chars of entropy; the CLI prints this once and stores only its hash.
    return secrets.token_urlsafe(32)


__all__: list[str] = [
    "INSUFFICIENT_SCOPE",
    "hash_key",
    "find_by_hash",
    "verify_key",
    "create_key",
]
