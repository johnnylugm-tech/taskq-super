"""FR-01 — unit-level coverage for FR-01 service / API edge paths.

The integration test file (`test_fr01.py`) only exercises the happy paths
that drive the FastAPI app end-to-end. Because the harness-generated test
file consolidates the `limit` boundary scenarios (cases 8/9/10) into a
single canonical test function name — and Python only keeps the last
definition — the `InvalidLimit` 422 branch and the `delete_task` 404
branch are not exercised via the integration surface.

These unit tests drive the service-layer functions directly, so the
coverage tool sees every branch on the FR-01 module surface.

[FR-01] / NFR-10 (integration surface stays in test_fr01.py).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

# Reuse the autouse DB session fixture from the existing conftest.
# NFR-10: import path manipulation is part of the same harness.
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from taskq_api.errors import TaskQError  # noqa: E402
from taskq_api.service.tasks import (  # noqa: E402
    InvalidLimit,
    create_task,
    delete_task,
    get_task,
    list_tasks,
)


# ---------------------------------------------------------------------------
# Service-layer tests covering FR-01 AC-1.7 (409 duplicate),
# AC-1.8 (422 limit out of bounds), AC-1.10 (delete 404).
# ---------------------------------------------------------------------------


def test_unit_create_task_duplicate_name_raises_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1.7: second insert with the same name raises a 409 problem+json.

    Directly drives `create_task` with a tiny in-memory store so the
    duplicate-name KeyError propagates exactly as in production.
    """
    # Same in-memory shape as the autouse fixture, but a private store for
    # this test so the duplicate-name path is deterministic.
    class _Store:
        def __init__(self) -> None:
            self.tasks: Dict[str, Dict[str, Any]] = {}

        def insert(self, row: Dict[str, Any]) -> None:
            for e in self.tasks.values():
                if e.get("name") == row.get("name"):
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
        ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            return [], None

    class _C:
        def __init__(self, s: _Store) -> None:
            self._s = s

        def __enter__(self) -> _Store:
            return self._s

        def __exit__(self, *exc: Any) -> bool:
            return False

    store = _Store()
    import taskq_api.repository.session as session_mod
    monkeypatch.setattr(session_mod, "transactional", lambda: _C(store))

    create_task(name="dup", command="echo")
    with pytest.raises(TaskQError) as info:
        create_task(name="dup", command="echo")
    assert info.value.status == 409
    assert info.value.title == "Conflict"


def test_unit_create_task_non_duplicate_keyerror_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defensive re-raise branch in `create_task` is exercised when
    the store raises a KeyError that is NOT a duplicate_name."""
    class _Store:
        def insert(self, row: Dict[str, Any]) -> None:
            raise KeyError("some_other_reason")

        def get(self, task_id: str) -> Optional[Dict[str, Any]]:
            return None

        def delete(self, task_id: str) -> bool:
            return False

        def list_paginated(
            self,
            cursor: Optional[str],
            limit: int,
            status: Optional[str],
        ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            return [], None

    class _C:
        def __init__(self, s: _Store) -> None:
            self._s = s

        def __enter__(self) -> _Store:
            return self._s

        def __exit__(self, *exc: Any) -> bool:
            return False

    import taskq_api.repository.session as session_mod
    monkeypatch.setattr(session_mod, "transactional", lambda: _C(_Store()))

    with pytest.raises(KeyError) as info:
        create_task(name="x", command="echo")
    assert info.value.args == ("some_other_reason",)


def test_unit_list_tasks_limit_below_minimum_raises_invalid_limit() -> None:
    """AC-1.8 lower-bound: limit=0 is below `min_limit=1` -> InvalidLimit."""
    with pytest.raises(InvalidLimit):
        list_tasks(limit=0, min_limit=1, max_limit=200)


def test_unit_list_tasks_limit_above_maximum_raises_invalid_limit() -> None:
    """AC-1.8 upper-bound: limit=201 is above `max_limit=200` -> InvalidLimit."""
    with pytest.raises(InvalidLimit):
        list_tasks(limit=201, min_limit=1, max_limit=200)


def test_unit_delete_task_not_found_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1.10: delete on an unknown id raises a 404 problem+json."""
    class _Store:
        def insert(self, row: Dict[str, Any]) -> None:
            pass

        def get(self, task_id: str) -> Optional[Dict[str, Any]]:
            return None

        def delete(self, task_id: str) -> bool:
            return False

        def list_paginated(
            self,
            cursor: Optional[str],
            limit: int,
            status: Optional[str],
        ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            return [], None

    class _C:
        def __init__(self, s: _Store) -> None:
            self._s = s

        def __enter__(self) -> _Store:
            return self._s

        def __exit__(self, *exc: Any) -> bool:
            return False

    import taskq_api.repository.session as session_mod
    monkeypatch.setattr(session_mod, "transactional", lambda: _C(_Store()))

    with pytest.raises(TaskQError) as info:
        delete_task("00000000-0000-0000-0000-000000000000")
    assert info.value.status == 404
    assert info.value.title == "Not Found"


def test_unit_get_task_not_found_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1.5: get on an unknown id raises a 404 problem+json."""
    class _Store:
        def insert(self, row: Dict[str, Any]) -> None:
            pass

        def get(self, task_id: str) -> Optional[Dict[str, Any]]:
            return None

        def delete(self, task_id: str) -> bool:
            return False

        def list_paginated(
            self,
            cursor: Optional[str],
            limit: int,
            status: Optional[str],
        ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            return [], None

    class _C:
        def __init__(self, s: _Store) -> None:
            self._s = s

        def __enter__(self) -> _Store:
            return self._s

        def __exit__(self, *exc: Any) -> bool:
            return False

    import taskq_api.repository.session as session_mod
    monkeypatch.setattr(session_mod, "transactional", lambda: _C(_Store()))

    with pytest.raises(TaskQError) as info:
        get_task("00000000-0000-0000-0000-000000000000")
    assert info.value.status == 404


@pytest.mark.asyncio
async def test_unit_api_list_tasks_invalid_limit_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1.8: GET /v1/tasks?limit=0 — the API layer catches InvalidLimit
    and returns 422 problem+json. Exercises the api/tasks.py
    `except InvalidLimit` branch (lines 67-68)."""
    import httpx
    from taskq_api.app import app

    # Replace the autouse session with a no-op store; the InvalidLimit is
    # raised by the service layer BEFORE the store is touched.
    class _Store:
        def insert(self, row: Dict[str, Any]) -> None:
            pass

        def get(self, task_id: str) -> Optional[Dict[str, Any]]:
            return None

        def delete(self, task_id: str) -> bool:
            return False

        def list_paginated(
            self,
            cursor: Optional[str],
            limit: int,
            status: Optional[str],
        ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
            return [], None

    class _C:
        def __init__(self, s: _Store) -> None:
            self._s = s

        def __enter__(self) -> _Store:
            return self._s

        def __exit__(self, *exc: Any) -> bool:
            return False

    import taskq_api.repository.session as session_mod
    monkeypatch.setattr(session_mod, "transactional", lambda: _C(_Store()))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.get(
            "/v1/tasks",
            params={"limit": 0},
            headers={"X-API-Key": "sk-test-read-key"},
        )
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
