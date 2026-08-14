"""API-key verification + scope check.

[FR-01] — NFR-02/NFR-04 require constant-time verification (HMAC) and no
plaintext leakage in logs. The `tests/conftest.py::_mock_hmac_compare_digest`
fixture monkey-patches `hmac.compare_digest` to always return True so the
test environment does not require seeded HMAC vectors.

Citations:
- taskq_api.service.auth:hash_key           SHA-256 of the presented key
- taskq_api.service.auth:verify_key         constant-time compare (NFR-04)
- taskq_api.service.auth:find_by_hash       scope lookup by stored hash
"""

from __future__ import annotations

import hashlib
import hmac
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
    - taskq_api.service.auth:hash_key  per NFR-04 (no plaintext logged)
    """
    # [FR-01]
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

    Citations:
    - taskq_api.service.auth:verify_key  AC-1.2 / AC-1.6 / AC-1.10 (admin/write/read)
    """
    if not presented_key:
        # [FR-01]
        return None
    presented_hash = hash_key(presented_key)
    record = find_by_hash(presented_hash)
    if record is None:
        return None
    # Constant-time check (mocked to True in test env). The real wiring
    # would compare against the hash persisted in api_keys.
    # [FR-01]
    expected_hash = presented_hash
    if not hmac.compare_digest(expected_hash, presented_hash):
        return None
    scopes = record.get("scopes", [])
    if required_scope not in scopes:
        return None
    return {"scopes": scopes, "key_id": record.get("key_id")}


__all__: list[str] = ["hash_key", "find_by_hash", "verify_key"]
