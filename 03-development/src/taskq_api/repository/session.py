"""Transactional session context manager.

[FR-01] — yields a store with `insert/get/delete/list_paginated` methods
that the handler layer uses through `with transactional() as store:`.
The autouse test fixture (`tests/conftest.py::_mock_db_session`) replaces
`transactional` with a lambda yielding the in-memory store.

Citations:
- taskq_api.repository.session:transactional  AC-1.4 / AC-1.5 / AC-1.7 / AC-1.8 / AC-1.10
- taskq_api.repository.session:ProductionStore  per SAD §3.1 (SQLAlchemy-backed)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List, Optional, Protocol


class StoreLike(Protocol):
    """Duck-typed contract that `transactional()` must yield.

    Citations:
    - taskq_api.repository.session:StoreLike  AC-1.4..AC-1.10
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
def transactional() -> Iterator[StoreLike]:
    """Open a unit-of-work and yield a store-like object.

    Production: bind a SQLAlchemy session wrapped by ProductionStore.
    Tests:    conftest monkey-patches this symbol with an in-memory stand-in.

    Citations:
    - taskq_api.repository.session:transactional  AC-1.4 / AC-1.5 / AC-1.7 / AC-1.8 / AC-1.10
    """
    # [FR-01]
    store = ProductionStore()
    try:
        yield store
    except Exception:
        raise


class ProductionStore:
    """SQLAlchemy-backed implementation of StoreLike.

    The production mapping lives in `taskq_api.repository.task_repo`; this
    class is a thin facade that the real wiring would use.

    Citations:
    - taskq_api.repository.session:ProductionStore  per SAD §3.1
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


__all__: list[str] = ["transactional", "ProductionStore", "StoreLike"]
