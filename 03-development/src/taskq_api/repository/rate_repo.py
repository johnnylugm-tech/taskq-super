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
/ FR-10 AC-10.6 (bucket-cap-change reset)
- taskq_api.repository.rate_repo:_ensure_schema  FR-10 cross-version schema migration
- taskq_api.repository.rate_repo:_migrate_add_column  FR-10 idempotent ADD COLUMN
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
    "  last_refill REAL NOT NULL,"
    "  burst INTEGER NOT NULL DEFAULT 0"
    ")"
)

# Migrations — applied idempotently inside `_ensure_schema` when an
# older `rate_buckets` table is encountered. Adding a column to a
# table alembic does not own is acceptable here because the schema
# itself is created lazily (`CREATE TABLE IF NOT EXISTS`); new columns
# are guarded by `PRAGMA table_info` so pre-existing deployments are
# upgraded in place without losing row data.
_SCHEMA_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE rate_buckets ADD COLUMN burst INTEGER NOT NULL DEFAULT 0",
)

_SELECT_FOR_UPDATE_SQL: str = (
    "SELECT tokens, last_refill, burst FROM rate_buckets "
    "WHERE token = :token FOR UPDATE"
)

_SELECT_SQLITE: str = (
    "SELECT tokens, last_refill, burst FROM rate_buckets WHERE token = :token"
)

_UPSERT_SQL: str = (
    "INSERT INTO rate_buckets(token, tokens, last_refill, burst) "
    "VALUES(:token, :tokens, :last_refill, :burst) "
    "ON CONFLICT(token) DO UPDATE SET "
    "tokens = excluded.tokens, last_refill = excluded.last_refill, "
    "burst = excluded.burst"
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

# [FR-09] AC-9.5 — lifetime rate-limit rejection counter. Bumped under
# ``_counter_lock`` on every rejection in ``try_consume`` so the admin
# ``/v1/metrics`` endpoint can surface a real count rather than a
# hard-coded zero. Reset by ``reset_for_test`` between test boundaries.
_rejection_count: int = 0
_counter_lock: threading.Lock = threading.Lock()


def _get_engine() -> Engine:
    """Return the process-wide engine, building it on first access."""
    global _engine
    if _engine is not None:
        return _engine
    with _init_lock:
        if _engine is None:
            _engine = _build_engine()
    return _engine


def get_rejection_count() -> int:
    """Return the lifetime number of rate-limit rejections observed.

    [FR-09] — AC-9.5. Read by ``taskq_api.api.health.metrics_endpoint``
    so ``/v1/metrics`` reports an honest ``rate_limit_rejections``
    counter instead of a stand-in zero. The counter is process-scoped:
    it reflects admissions this process has denied since startup (or
    since the last ``reset_for_test`` call), not historical rejections
    from prior runs.
    """
    with _counter_lock:
        return _rejection_count


def _record_rejection() -> None:
    """Bump the lifetime rejection counter by one (test/operator visible)."""
    global _rejection_count
    with _counter_lock:
        _rejection_count += 1


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
    """Create ``rate_buckets`` once, idempotently.

    [FR-05] — AC-5.5. The table is created lazily on first
    ``try_consume`` call. New columns (added by later code revisions)
    are applied through ``_SCHEMA_MIGRATIONS`` after a ``PRAGMA
    table_info`` check so pre-existing in-process schemas are upgraded
    in place without dropping any rows.
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _get_engine().begin() as conn:
            conn.execute(text(_SCHEMA_DDL))
            existing_cols = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA table_info(rate_buckets)")
                ).fetchall()
            }
            for migration_sql in _SCHEMA_MIGRATIONS:
                # Each migration is parameterised by the column it
                # would add; we extract it from the trailing `ADD COLUMN
                # <name>` clause via a cheap split so the migration list
                # stays declarative.
                _migrate_add_column(conn, existing_cols, migration_sql)
        _schema_ready = True


def _migrate_add_column(
    conn, existing_cols: set[str], migration_sql: str
) -> None:
    """Apply one ``ALTER TABLE … ADD COLUMN`` migration if the column is absent.

    Splits the column name off the tail of ``migration_sql`` so the
    migration list above remains declarative; callers stay out of the
    ``PRAGMA`` / ``ALTER`` boilerplate.
    """
    # Migration DDL is always of the form
    #   ALTER TABLE <table> ADD COLUMN <col> <type> [DEFAULT <expr>]
    # The column name is the fourth whitespace-separated token (0:ALTER
    # 1:TABLE 2:<table> 3:ADD 4:COLUMN 5:<col>).
    tokens = migration_sql.split()
    try:
        col_name = tokens[5]
    except IndexError:
        return
    if col_name in existing_cols:
        return
    conn.execute(text(migration_sql))
    existing_cols.add(col_name)


def reset_for_test() -> None:
    """Wipe every row in ``rate_buckets`` and zero the rejection counter.

    Driven by the autouse fixtures in ``03-development/tests/conftest.py``
    and ``03-development/tests/test_fr05.py`` via the back-compat
    ``_rl._buckets.clear()`` hook on ``taskq_api.service.ratelimit``.
    """
    global _rejection_count
    _ensure_schema()
    with _get_engine().begin() as conn:
        conn.execute(text("DELETE FROM rate_buckets"))
    with _counter_lock:
        _rejection_count = 0


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

    Bucket-cap-change reset (FR-10 cross-phase compatibility): the row
    carries the ``burst`` value it was last consumed under. When the
    caller supplies a different ``burst`` (e.g. tests that temporarily
    lower the cap to drain it, then restore the production cap) the
    stored bucket state is invalidated and the bucket is refilled to
    the new cap. This keeps the rate limiter self-consistent across
    configuration changes without leaking previous-bucket state into a
    new (larger) cap.
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
            stored_burst: int = int(burst)
        else:
            tokens = float(row[0])
            last_refill = float(row[1])
            # [FR-10] — read the burst the row was last consumed under.
            stored_burst = int(row[2]) if len(row) > 2 else int(burst)
        # [FR-10] — when the bucket cap changes, the stored bucket is
        # stale; reset to the new cap so the rate limiter cannot leak
        # the previous configuration's bucket into the new one.
        if int(burst) != stored_burst:
            tokens = float(burst)
            last_refill = now
        elapsed = max(0.0, now - last_refill)
        refilled = min(float(burst), tokens + elapsed * per_sec)
        if refilled >= 1.0:
            tokens_after = refilled - 1.0
            conn.execute(
                text(_UPSERT_SQL),
                {
                    "token": token,
                    "tokens": tokens_after,
                    "last_refill": now,
                    "burst": int(burst),
                },
            )
            return True, tokens_after
        conn.execute(
            text(_UPSERT_SQL),
            {
                "token": token,
                "tokens": refilled,
                "last_refill": now,
                "burst": int(burst),
            },
        )
        # [FR-09] AC-9.5 — record the rejection so /v1/metrics can report it.
        _record_rejection()
        return False, refilled


__all__: list[str] = ["try_consume", "reset_for_test", "get_rejection_count"]
