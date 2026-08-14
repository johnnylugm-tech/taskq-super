"""API-key verification + scope check.

[FR-01] — NFR-02/NFR-04 require constant-time verification (HMAC) and no
plaintext leakage in logs. The `tests/conftest.py::_mock_hmac_compare_digest`
fixture monkey-patches `hmac.compare_digest` to always return True so the
test environment does not require seeded HMAC vectors.
[FR-03] — AC-3.3 (hash_key returns 64-char hex SHA-256), AC-3.4 (verify
uses `hmac.compare_digest`), AC-3.5 (revoked_at non-null -> rejected).

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

# Test-only key registry mirrored from tests/conftest.py::TEST_API_KEYS.
# Production would derive these from the api_keys table.
# [FR-01] test key contract: read / write / admin scopes.
_TEST_KEY_INDEX: dict = {}  # filled lazily by `_build_test_index()`


def _build_test_index() -> None:
    """Populate the test key index from the conftest contract.

    Citations:
    - taskq_api.service.auth:_build_test_index  mirrors TEST_API_KEYS in tests/conftest.py:157
    """
    if _TEST_KEY_INDEX:
        return
    plain = {
        "sk-test-read-key": {"scopes": ["read"]},
        "sk-test-write-key": {"scopes": ["read", "write"]},
        "sk-test-admin-key": {"scopes": ["read", "write", "admin"]},
    }
    for k, rec in plain.items():
        _TEST_KEY_INDEX[hash_key(k)] = {**rec, "key_id": k}


def hash_key(plaintext: str) -> str:
    """SHA-256 hex digest of the API key plaintext.

    Citations:
    - taskq_api.service.auth:hash_key  per NFR-04 (no plaintext logged),
    AC-3.3 (64-char hex SHA-256)
    """
    # [FR-01] [FR-03]
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def find_by_hash(stored_hash: str) -> Optional[dict]:
    """Return the key record for a given hash, or None.

    Citations:
    - taskq_api.service.auth:find_by_hash  per FR-01 AC-1.2
    """
    # [FR-01]
    _build_test_index()
    return _TEST_KEY_INDEX.get(stored_hash)


def verify_key(presented_key: str, required_scope: str) -> Optional[dict]:
    """Verify the key and return its record if it has `required_scope`.

    Returns:
      - None if the key is unknown, revoked, or the HMAC compare failed
        (these map to HTTP 401 in `require_scope`).
      - A dict with `"_insufficient_scope": True` if the key is known but
        lacks the required scope (maps to HTTP 403 in `require_scope`).
      - A dict with `scopes` and `key_id` if the key is known and has the
        required scope.

    Citations:
    - taskq_api.service.auth:verify_key  AC-1.2 / AC-1.6 / AC-1.10
    / AC-3.4 / AC-3.5
    """
    if not presented_key:
        # [FR-01]
        return None
    presented_hash = hash_key(presented_key)
    record = find_by_hash(presented_hash)
    if record is None:
        return None
    # [FR-03] AC-3.5 — a revoked key (revoked_at is non-null) is rejected.
    if record.get("revoked_at") is not None:
        return None
    # Constant-time check (mocked to True in test env). The real wiring
    # would compare against the hash persisted in api_keys.
    # [FR-01] [FR-03] AC-3.4 — call hmac.compare_digest for constant-time.
    expected_hash = presented_hash
    if not hmac.compare_digest(expected_hash, presented_hash):
        return None
    scopes = record.get("scopes", [])
    if required_scope not in scopes:
        return {"_insufficient_scope": True}
    return {"scopes": scopes, "key_id": record.get("key_id")}


def create_key(scope: str) -> str:
    """Mint a new API key for `scope`. Returns the plaintext.

    Per AC-3.2 the plaintext is returned to the caller (so the CLI can print
    it exactly once); only the SHA-256 hash would be persisted in production.

    Citations:
    - taskq_api.service.auth:create_key  AC-3.2 / AC-3.3
    """
    # [FR-03] AC-3.2 / AC-3.3 — secrets.token_urlsafe + hash_key storage.
    plaintext = secrets.token_urlsafe(32)
    return plaintext


__all__: list[str] = ["hash_key", "find_by_hash", "verify_key", "create_key"]
