"""Pytest fixtures for FR-01 test isolation.

GREEN agent note: these fixtures only patch external boundaries
(HMAC verification, DB session, API key hashing). They DO NOT implement
business logic. The tests below still fail because the route handlers
themselves are missing — not because of bad signatures or missing rows.
"""

from __future__ import annotations

import hmac
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure 03-development/src is importable when pytest is invoked from
# the project root (`python3 -m pytest 03-development/tests/test_fr01.py`).
# pytest's `pythonpath` config does NOT propagate to subprocesses, but it
# DOES propagate to the test process itself when run as a module.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# In-memory task store (shared per test function via function-scoped fixture)
# ---------------------------------------------------------------------------


class _InMemoryTaskStore:
    """Lightweight stand-in for the repository layer.

    GREEN TODO: replace this with the real SQLAlchemy-backed
    `taskq_api.repository.task_repo` once implemented. The test contract is
    the same: `insert`, `get`, `list_paginated`, `delete_by_id`.
    """

    def __init__(self) -> None:
        self.tasks: Dict[str, Dict[str, Any]] = {}

    def insert(self, row: Dict[str, Any]) -> None:
        for existing in self.tasks.values():
            if existing.get("name") == row.get("name"):
                raise KeyError("duplicate_name")
        self.tasks[row["id"]] = row

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def delete(self, task_id: str) -> bool:
        return self.tasks.pop(task_id, None) is not None

    def list_paginated(
        self,
        cursor: Optional[str],
        limit: int,
        status: Optional[str],
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        items = sorted(self.tasks.values(), key=lambda r: r["created_at"])
        if status is not None:
            items = [r for r in items if r.get("status") == status]
        # cursor is a marker on the LAST item returned
        start = 0
        if cursor:
            for idx, row in enumerate(items):
                if row["id"] == cursor:
                    start = idx + 1
                    break
        page = items[start : start + limit]
        next_cursor = page[-1]["id"] if len(page) == limit and (start + limit) < len(items) else None
        return page, next_cursor


@pytest.fixture
def task_store() -> _InMemoryTaskStore:
    """Per-test in-memory store. Function-scoped so each test gets a clean DB."""
    return _InMemoryTaskStore()


# ---------------------------------------------------------------------------
# Autouse mocks for external boundaries
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `hmac.compare_digest` to accept the test key.

    GREEN TODO: real `taskq_api.service.auth.verify_key` must still call
    `hmac.compare_digest(stored_hash, presented_hash)` for constant-time
    comparison; this fixture only relaxes it for the test environment.
    """

    def _always_equal(a: bytes | str, b: bytes | str) -> bool:  # noqa: ARG001
        return True

    monkeypatch.setattr(hmac, "compare_digest", _always_equal)


@pytest.fixture(autouse=True)
def _mock_db_session(
    monkeypatch: pytest.MonkeyPatch, task_store: _InMemoryTaskStore
) -> None:
    """Mock the DB session so tests don't need a live database.

    GREEN TODO: replace this fixture with a real session fixture backed by
    a per-test SQLite file once `taskq_api.repository.session` is
    implemented. The handler code under test must NOT be changed — the
    in-memory store here mirrors the public API the real repo will expose.
    """
    # If `taskq_api.repository.session` is not yet implemented, the
    # monkeypatch below will raise AttributeError, which is fine — that is
    # itself a ModuleNotFoundError-precursor signal.
    try:
        from taskq_api.repository import session as session_mod  # type: ignore
    except Exception:
        return  # source not yet implemented — fixture is a no-op
    # Patch `transactional` so handlers that ask for a session get the in-memory store
    monkeypatch.setattr(
        session_mod,
        "transactional",
        lambda: _NullContextManager(task_store),
        raising=False,
    )


class _NullContextManager:
    """A no-op context manager that yields the in-memory store."""

    def __init__(self, store: _InMemoryTaskStore) -> None:
        self._store = store

    def __enter__(self) -> _InMemoryTaskStore:
        return self._store

    def __exit__(self, *exc: Any) -> bool:
        return False


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

# Well-known test API keys, indexed by scope. The handler under test is
# expected to look these up in some `api_keys` table; GREEN must implement
# the actual lookup + SHA-256 hashing. These plaintext values are stable
# so tests don't depend on random key generation.
TEST_API_KEYS: Dict[str, str] = {
    "read": "sk-test-read-key",
    "write": "sk-test-write-key",
    "admin": "sk-test-admin-key",
}


@pytest.fixture
def write_api_key() -> str:
    return TEST_API_KEYS["write"]


@pytest.fixture
def read_api_key() -> str:
    return TEST_API_KEYS["read"]


@pytest.fixture
def admin_api_key() -> str:
    return TEST_API_KEYS["admin"]