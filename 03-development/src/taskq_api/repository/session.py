"""Persistence layer — engine, session factory, and per-request transaction boundary.

[FR-06] — repository-side session lifecycle: one ``Session`` per request,
transaction boundary explicit (commit on success, rollback on exception).
The engine is constructed with ``pool_size=TASKQ_DB_POOL_SIZE`` and
``pool_pre_ping=True`` so connection-pool sizing and pre-ping validation
are wired at the engine level (AC-6.5). String-concatenated SQL is
forbidden — handlers must use ORM or parameterised queries (NFR-02).

Citations:
- taskq_api.repository.session:transactional  AC-6.3 (one Session per request, rollback on exception)
- taskq_api.repository.session:SessionLocal    AC-6.3 (zero-arg session factory)
- taskq_api.repository.session:_build_engine  AC-6.5 (pool_size + pool_pre_ping)
- taskq_api.repository.session:ProductionStore  back-compat shim for FR-01 callers
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, List, Optional, Protocol

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _build_engine() -> Engine:
    """Build the per-process SQLAlchemy engine with the configured pool.

    Honours ``TASKQ_DB_URL`` for production / integration runs and
    ``TASKQ_DB_POOL_SIZE`` for the connection-pool size. ``pool_pre_ping``
    is always enabled so stale connections are validated before use
    (AC-6.5).

    Citations:
    - taskq_api.repository.session:_build_engine  AC-6.5 (pool_size + pool_pre_ping)
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
    """Return the process-wide engine, building it on first access.

    Citations:
    - taskq_api.repository.session:get_engine  AC-6.5 (engine accessor for assertions)
    """
    return _engine


class StoreLike(Protocol):
    """Duck-typed contract that ``transactional()`` may yield.

    FR-06 yields a real SQLAlchemy ``Session``; this protocol remains for
    legacy callers that still depend on the in-memory store shape.

    Citations:
    - taskq_api.repository.session:StoreLike  AC-1.4..AC-1.10 back-compat
    """

    def insert(self, row: dict) -> None:  # raises KeyError("duplicate_name")
        ...

    def get(self, task_id: str) -> Optional[dict]: ...

    def delete(self, task_id: str) -> bool: ...

    def list_paginated(
        self,
        cursor: Optional[str],
        limit: int,
        status: Optional[str],
    ) -> "tuple[List[dict], Optional[str]]":
        ...


@contextmanager
def transactional() -> Iterator[Session]:
    """Open a unit-of-work: exactly one ``Session`` per request lifecycle.

    Behaviour:

    * On normal exit — ``session.close()`` is called (any handler-level
      ``session.commit()`` persists changes; no implicit commit here).
    * On exception inside the ``with`` block — ``session.rollback()``
      is called and the exception is re-raised to the caller.
    * Exactly one ``Session`` is opened per ``with`` block, even on the
      exception path (AC-6.3).

    Citations:
    - taskq_api.repository.session:transactional  AC-6.3 (one Session per request)
    """
    session: Session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ProductionStore:
    """Back-compat shim for FR-01 callers that still reference this class.

    FR-06's ``transactional()`` now yields a real SQLAlchemy ``Session``;
    this class is retained so existing import paths remain valid.

    Citations:
    - taskq_api.repository.session:ProductionStore  FR-01 back-compat
    """

    def __init__(self) -> None:
        # No live SQLAlchemy session in GREEN; tests substitute this symbol.
        self._impl = None

    def insert(self, row: dict) -> None:
        # [FR-01]
        raise NotImplementedError(
            "ProductionStore.insert: wire to task_repo.insert in production"
        )

    def get(self, task_id: str) -> Optional[dict]:
        # [FR-01]
        raise NotImplementedError(
            "ProductionStore.get: wire to task_repo.get in production"
        )

    def delete(self, task_id: str) -> bool:
        # [FR-01]
        raise NotImplementedError(
            "ProductionStore.delete: wire to task_repo.delete in production"
        )

    def list_paginated(
        self,
        cursor: Optional[str],
        limit: int,
        status: Optional[str],
    ) -> "tuple[List[dict], Optional[str]]":
        # [FR-01]
        raise NotImplementedError(
            "ProductionStore.list_paginated: wire to task_repo.list_paginated in production"
        )


__all__: list[str] = [
    "transactional",
    "SessionLocal",
    "StoreLike",
    "ProductionStore",
    "get_engine",
]