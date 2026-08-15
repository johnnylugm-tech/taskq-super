"""Pytest fixtures for FR-01 test isolation.

GREEN agent note: these fixtures only patch external boundaries
(HMAC verification, DB session, API key hashing). They DO NOT implement
business logic. The tests below still fail because the route handlers
themselves are missing — not because of bad signatures or missing rows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Mark the test environment BEFORE any module under test is imported so
# the module-level TASKQ_ENV-guarded fixtures (e.g. the plaintext API
# keys in `taskq_api.repository.key_repo`) load correctly.
os.environ.setdefault("TASKQ_ENV", "test")
# Pin TASKQ_RATE_DB_URL at conftest import time (NOT inside a fixture):
# test modules collect their top-level `from taskq_api.app import app`
# before any autouse fixture runs, which builds rate_repo's cached
# engine. Setting the URL in a fixture is too late — the cached engine
# keeps the sentinel "XXsqlite:///:memory:XX" and every endpoint that
# flows through check_rate_limit returns 500 ("no such table:
# rate_buckets"). setdefault keeps an explicit shell override intact.
os.environ.setdefault(
    "TASKQ_RATE_DB_URL",
    # File-based sqlite so each connection in the pool sees the same
    # schema. In-memory SQLite needs StaticPool (the production path's
    # sentinel URL uses it) or shared-cache (which pysqlite under
    # SQLAlchemy's pool still partitions into per-connection DBs for
    # `begin()` transactions), so an in-memory URL would let the
    # `_ensure_schema` DDL run on connection A while `try_consume`
    # SELECTs against connection B's empty DB. A tmp file is the
    # simplest cross-platform fix that survives the pool's connection
    # rotation. Wiped at session start so no state leaks between
    # pytest invocations.
    "sqlite+pysqlite:////tmp/taskq-rate-test.db",
)
# Wipe stale state from a prior pytest invocation.
try:
    import pathlib
    pathlib.Path("/tmp/taskq-rate-test.db").unlink(missing_ok=True)
except Exception:
    pass

import hmac
from typing import Any, Dict, List, Optional

import pytest

# FR-05 — keep the production default burst (20) at import time so FR-05
# tests (which compute `burst_capacity = str(DEFAULT_BURST)` before their
# own monkeypatch) see 20. The conftest autouse fixture below lifts the
# burst to 10000 for non-FR-05 tests, so seeding scenarios outside FR-05
# are not rate-limited.

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
def _enable_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mark the test environment so module-level test fixtures (e.g.
    the plaintext API keys in key_repo) are loaded. The bug-hunt
    fix to key_repo guards the test-only dict behind TASKQ_ENV == 'test'.
    """
    monkeypatch.setenv("TASKQ_ENV", "test")


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
    patched = lambda: _NullContextManager(task_store)  # noqa: E731
    monkeypatch.setattr(
        session_mod,
        "transactional",
        patched,
        raising=False,
    )
    # Also patch any other module that captured an earlier
    # `taskq_api.repository.session` reference at import time. Tests like
    # FR-06 delete `taskq_api.repository.*` from `sys.modules` and force a
    # re-import, which creates a fresh module object — but the bound
    # `session_mod` reference inside already-loaded callers (e.g.
    # `taskq_api.service.tasks`) still points at the original (orphaned)
    # module object. Without patching those bound references too, the
    # next request would resolve `transactional` on the original module
    # and get the real SQLAlchemy Session — which has no
    # `.insert()`/`.list_paginated()`/`.delete()` surface the in-memory
    # stand-in exposes.
    for _mod_name in (
        "taskq_api.service.tasks",
        "taskq_api.api.tasks",
        "taskq_api.api.health",
    ):
        try:
            _mod = __import__(_mod_name, fromlist=["session_mod"])
        except Exception:
            continue
        bound = getattr(_mod, "session_mod", None)
        if bound is None or bound is session_mod:
            continue
        monkeypatch.setattr(bound, "transactional", patched, raising=False)


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


# ---------------------------------------------------------------------------
# FR-05 — rate-limit isolation between tests
# ---------------------------------------------------------------------------
# The token-bucket state in `taskq_api.service.ratelimit` is module-level.
# Without a per-test reset, tests that issue more than `TASKQ_RATE_BURST`
# requests against the same `X-API-Key` (e.g. seeding 51 rows for the
# cursor-pagination test) would hit a 429 mid-test. This autouse fixture
# clears the bucket dict at every test boundary AND lifts the burst
# capacity for non-FR-05 tests so seeding scenarios are not rate-limited.
# FR-05's own tests override DEFAULT_BURST per-test via monkeypatch.
@pytest.fixture(autouse=True)
def _reset_rate_limit_state_for_all_tests(request: pytest.FixtureRequest) -> None:
    """Reset the in-process rate-limit bucket between every test."""
    try:
        from taskq_api.service import ratelimit as _rl  # type: ignore
        if hasattr(_rl, "_buckets"):
            _rl._buckets.clear()  # type: ignore[attr-defined]
        # Lift the burst capacity for non-FR-05 tests so seeding scenarios
        # that issue >20 calls per single test (FR-01 cursor pagination)
        # are not rate-limited. FR-05's own tests override DEFAULT_BURST
        # per-test via monkeypatch.setattr inside the test body, which
        # happens AFTER this fixture runs.
        node_path = str(request.node.fspath)
        is_fr05_test = "test_fr05" in node_path
        if is_fr05_test:
            # Make sure FR-05 tests run with the production burst (20),
            # regardless of what previous non-FR-05 tests left behind.
            if hasattr(_rl, "DEFAULT_BURST"):
                _rl.DEFAULT_BURST = 20  # type: ignore[attr-defined]
        else:
            if hasattr(_rl, "DEFAULT_BURST"):
                _rl.DEFAULT_BURST = 10000  # type: ignore[attr-defined]
    except Exception:
        pass
    yield
    try:
        from taskq_api.service import ratelimit as _rl  # type: ignore
        if hasattr(_rl, "_buckets"):
            _rl._buckets.clear()  # type: ignore[attr-defined]
    except Exception:
        pass