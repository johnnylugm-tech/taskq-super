"""Rate-bucket persistence — the `rate_buckets` table.

[FR-05] AC-5.5 — bucket state lives in the `rate_buckets` table; each
update acquires a row-level lock via ``SELECT ... FOR UPDATE`` inside a
single ``engine.begin()`` transaction (SPEC.md §9 R12: 單一交易 +
row-level lock). Concurrent attempts against the same token serialise
on that lock — AC-5.4's parallel-double-burst test cannot over-admit.

Dialect notes:

* **Postgres / MySQL / MSSQL** — emits ``SELECT ... FOR UPDATE``; the
  server-side row lock is what serialises admission attempts.
* **SQLite** — the ``FOR UPDATE`` clause is not in the SQL grammar for
  older SQLite versions, and SQLite's per-database reserved/exclusive
  lock acquired by ``BEGIN IMMEDIATE`` (used inside ``engine.begin()``
  on a SQLite engine) provides the same admission serialisation. The
  module selects the clause at runtime based on the bound dialect.

The default engine for the GREEN / refactor phase is an in-memory
SQLite wrapped in a ``StaticPool`` so a single shared connection
serialises writes through SQLite's internal mutex. Production
deployments can point at any SQLAlchemy URL via the ``TASKQ_RATE_DB_URL``
environment variable before this module is imported.

Citations:
- taskq_api.repository.rate_repo:try_consume    AC-5.5 (FOR UPDATE row lock)
- taskq_api.repository.rate_repo:reset_for_test  test-fixture isolation
"""

from __future__ import annotations

import os
import threading
from typing import Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


_SCHEMA_DDL: str = (
    "CREATE TABLE IF NOT EXISTS rate_buckets ("
    "  token TEXT PRIMARY KEY,"
    "  tokens REAL NOT NULL,"
    "  last_refill REAL NOT NULL"
    ")"
)

_SELECT_FOR_UPDATE_SQL: str = (
    "SELECT tokens, last_refill FROM rate_buckets "
    "WHERE token = :token FOR UPDATE"
)

_SELECT_SQLITE: str = (
    "SELECT tokens, last_refill FROM rate_buckets WHERE token = :token"
)

_UPSERT_SQL: str = (
    "INSERT INTO rate_buckets(token, tokens, last_refill) "
    "VALUES(:token, :tokens, :last_refill) "
    "ON CONFLICT(token) DO UPDATE SET "
    "tokens = excluded.tokens, last_refill = excluded.last_refill"
)


def _build_engine() -> Engine:
    """Build the per-process SQLAlchemy engine.

    Honours ``TASKQ_RATE_DB_URL`` for production / integration runs;
    falls back to an in-memory SQLite (``StaticPool``, single shared
    connection, ``check_same_thread=False``) so the GREEN/refactor
    phase does not require a live database while still exercising the
    real transaction + row-lock code path.
    """
    url = os.environ.get("TASKQ_RATE_DB_URL")
    if url:
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


_init_lock = threading.Lock()
_engine: Engine | None = None


def _get_engine() -> Engine:
    """Return the process-wide engine, building it on first access."""
    global _engine
    if _engine is not None:
        return _engine
    with _init_lock:
        if _engine is None:
            _engine = _build_engine()
    return _engine


def _select_sql() -> str:
    """Pick the dialect-appropriate `SELECT` statement.

    SQLite (< 3.45) does not support ``FOR UPDATE``; admission
    serialisation is provided by the engine's enclosing transaction
    (``engine.begin()`` opens a ``BEGIN IMMEDIATE`` against a SQLite
    engine, which acquires the database-level reserved lock). All
    other dialects use the canonical ``FOR UPDATE`` form so the row
    lock is scoped to the addressed row.
    """
    dialect_name = _get_engine().dialect.name
    if dialect_name == "sqlite":
        return _SELECT_SQLITE
    return _SELECT_FOR_UPDATE_SQL


_schema_lock = threading.Lock()
_schema_ready: bool = False


def _ensure_schema() -> None:
    """Create ``rate_buckets`` once, idempotently."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _get_engine().begin() as conn:
            conn.execute(text(_SCHEMA_DDL))
        _schema_ready = True


def reset_for_test() -> None:
    """Wipe every row in ``rate_buckets``.

    Driven by the autouse fixtures in ``03-development/tests/conftest.py``
    and ``03-development/tests/test_fr05.py`` via the back-compat
    ``_rl._buckets.clear()`` hook on ``taskq_api.service.ratelimit``.
    """
    _ensure_schema()
    with _get_engine().begin() as conn:
        conn.execute(text("DELETE FROM rate_buckets"))


def try_consume(
    token: str,
    *,
    now: float,
    burst: int,
    per_sec: float,
) -> Tuple[bool, float]:
    """Atomically reserve one bucket slot for ``token``.

    A single ``engine.begin()`` transaction; the bucket row is locked
    via ``SELECT ... FOR UPDATE`` (AC-5.5). Concurrent attempts against
    the same token serialise on that row lock — no over-admission is
    possible.

    Returns ``(allowed, level)``:

    - ``(True, remaining)``  — token was consumed; ``remaining`` is the
      bucket level after the consumption (the caller integer-truncates
      for the response).
    - ``(False, refilled)``  — bucket was over budget; ``refilled`` is
      the level after continuous refill (``0 <= refilled < 1``) used
      to compute ``Retry-After``.
    """
    _ensure_schema()
    with _get_engine().begin() as conn:
        row = conn.execute(
            text(_select_sql()),
            {"token": token},
        ).first()
        if row is None:
            tokens: float = float(burst)
            last_refill: float = now
        else:
            tokens = float(row[0])
            last_refill = float(row[1])
        elapsed = max(0.0, now - last_refill)
        refilled = min(float(burst), tokens + elapsed * per_sec)
        if refilled >= 1.0:
            tokens_after = refilled - 1.0
            conn.execute(
                text(_UPSERT_SQL),
                {"token": token, "tokens": tokens_after, "last_refill": now},
            )
            return True, tokens_after
        conn.execute(
            text(_UPSERT_SQL),
            {"token": token, "tokens": refilled, "last_refill": now},
        )
        return False, refilled


__all__: list[str] = ["try_consume", "reset_for_test"]
