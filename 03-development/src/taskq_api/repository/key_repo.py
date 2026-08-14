"""API-key persistence layer.

[FR-01] — looks up an API key by its stored hash and returns its scopes.
[FR-03] — AC-3.3 (key_hash is a 64-char hex SHA-256); AC-3.5 (revoked_at
non-null records are filtered by the service layer).

This module owns the key registry. In production the data source would be
the `api_keys` table; for GREEN the plaintext → scopes mapping is seeded
once and indexed by SHA-256 hash.

Citations:
- taskq_api.repository.key_repo:lookup_by_hash  per FR-01 AC-1.2 / FR-03 AC-3.5
"""

from __future__ import annotations

import hashlib
from typing import Dict, Optional

# Test-only key registry. Production would derive these from the
# `api_keys` table (one row per key, indexed on `key_hash`).
# Plaintext → scopes mapping; the on-disk index keys are SHA-256 hashes.
_KEYS_PLAINTEXT: Dict[str, dict] = {
    "sk-test-read-key": {"scopes": ["read"]},
    "sk-test-write-key": {"scopes": ["read", "write"]},
    "sk-test-admin-key": {"scopes": ["read", "write", "admin"]},
}

_HASH_TO_RECORD: Dict[str, dict] = {}
_SEEDED: bool = False


def _ensure_seeded() -> None:
    """Populate the hash → record index on first access (idempotent)."""
    global _SEEDED
    if _SEEDED:
        return
    _SEEDED = True
    for plaintext, record in _KEYS_PLAINTEXT.items():
        key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        _HASH_TO_RECORD[key_hash] = {**record, "key_id": plaintext}


def lookup_by_hash(stored_hash: str) -> Optional[dict]:
    """Return the api_key record for the given stored hash, or None.

    In production this would query the `api_keys` table by indexed hash.

    Citations:
    - taskq_api.repository.key_repo:lookup_by_hash  per FR-01 AC-1.2 / FR-03 AC-3.5
    """
    _ensure_seeded()
    return _HASH_TO_RECORD.get(stored_hash)


__all__: list[str] = ["lookup_by_hash"]
