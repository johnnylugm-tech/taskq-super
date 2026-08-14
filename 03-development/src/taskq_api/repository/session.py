"""Persistence layer — engine, session factory, and per-request transaction boundary.

[FR-06] — repository-side session lifecycle: one ``Session`` per request,
transaction boundary explicit (rollback on exception, no implicit commit).
The engine is constructed with ``pool_size=TASKQ_DB_POOL_SIZE`` and
``pool_pre_ping=True`` so connection-pool sizing and pre-ping validation
are wired at the engine level (AC-6.5). String-concatenated SQL is
forbidden — handlers must use ORM or parameterised queries (NFR-02).

Citations:
- taskq_api.repository.session:_build_engine  AC-6.5 (pool_size + pool_pre_ping)
- taskq_api.repository.session:SessionLocal    AC-6.3 (zero-arg session factory)
- taskq_api.repository.session:transactional  AC-6.3 (one Session per request, rollback on exception)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _build_engine() -> Engine:
    """Build the per-process SQLAlchemy engine with the configured pool.

    Honours ``TASKQ_DB_URL`` for production / integration runs and
    ``TASKQ_DB_POOL_SIZE`` for the connection-pool size. ``pool_pre_ping``
    is always enabled so stale connections are validated before use
    (AC-6.5).
    """
    url = os.environ.get("TASKQ_DB_URL", "sqlite+pysqlite:///:memory:")
    pool_size = int(os.environ.get("TASKQ_DB_POOL_SIZE", "5"))
    return sqlalchemy.create_engine(
        url,
        pool_size=pool_size,
        pool_pre_ping=True,
    )


_engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def get_engine() -> Engine:
    """Return the process-wide engine."""
    return _engine


@contextmanager
def transactional() -> Iterator[Session]:
    """Open a unit-of-work: exactly one ``Session`` per request lifecycle.

    Behaviour:

    * On normal exit — ``session.close()`` is called. Handlers that need
      to persist changes must call ``session.commit()`` themselves; this
      context manager does NOT auto-commit.
    * On exception inside the ``with`` block — ``session.rollback()``
      is called and the exception is re-raised to the caller.
    * Exactly one ``Session`` is opened per ``with`` block, even on the
      exception path (AC-6.3).
    """
    session: Session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__: list[str] = [
    "transactional",
    "SessionLocal",
    "get_engine",
]
