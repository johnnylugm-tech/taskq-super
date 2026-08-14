"""API-key lookup repository.

[FR-01] — looks up an API key by its stored hash and returns its scopes.

In production this would query an `api_keys` table; for GREEN the
handler delegates to `taskq_api.service.auth.verify_key` and treats the
in-memory store from conftest as the data source.

Citations:
- taskq_api.repository.key_repo:lookup_by_hash  per FR-01 NFR-04 (no plaintext key in logs)
"""

from __future__ import annotations

from typing import Optional


def lookup_by_hash(stored_hash: str) -> Optional[dict]:
    """Return the api_key record for a given stored hash, or None.

    The real implementation queries the api_keys table by indexed hash;
    GREEN delegates to `taskq_api.service.auth` to avoid SQL here.

    Citations:
    - taskq_api.repository.key_repo:lookup_by_hash  per FR-01 AC-1.2
    """
    # [FR-01]
    from taskq_api.service.auth import find_by_hash

    return find_by_hash(stored_hash)


__all__: list[str] = ["lookup_by_hash"]
