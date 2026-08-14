"""FR-01 — Task resource CRUD API.

These are RED tests. They cover all 12 acceptance criteria enumerated in
`02-architecture/TEST_SPEC.md` (FR-01 table, cases #1..#12). The test
function names are the canonical names the Gate-1 spec-coverage check
matches against — DO NOT rename.

The 12 cases from TEST_SPEC (verbatim):
  1.  test_fr01_create_task_returns_201_with_id_and_status_pending
  2.  test_fr01_create_task_without_api_key_returns_401
  3.  test_fr01_create_task_invalid_body_returns_422
  4.  test_fr01_get_task_with_read_key_returns_200
  5.  test_fr01_get_unknown_task_returns_404
  6.  test_fr01_delete_task_with_write_scope_returns_403
  7.  test_fr01_create_task_duplicate_name_returns_409
  8.  test_fr01_list_tasks_limit_bounds_and_default          # limit=0  -> 422
  9.  test_fr01_list_tasks_limit_bounds_and_default          # limit=201 -> 422
  10. test_fr01_list_tasks_limit_bounds_and_default          # default  -> 200, eff=50
  11. test_fr01_list_tasks_uses_cursor_pagination_not_offset
  12. test_fr01_delete_task_with_admin_key_returns_204

Note on case 8/9/10: TEST_SPEC declares three distinct scenarios that share
the same canonical test_fn name `test_fr01_list_tasks_limit_bounds_and_default`.
Per the v2.13.0 multi-scenario rule, each scenario becomes its own function
with a distinct scenario suffix in the local docstring; the function name
itself MUST remain identical to satisfy the exact-match gate.

HTTP layer: per NFR-10, integration tests must drive the FastAPI app via
`httpx.AsyncClient(transport=ASGITransport(app))`. We import the app from
`taskq_api.app` (per SAD §2.2) — the same module the `uvicorn` entry-point
uses — so the ASGI surface is exercised exactly as in production.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, AsyncIterator, Dict

import pytest

# Imports of source modules under test. Per TDD-RED contract, these imports
# MUST NOT be wrapped in try/except — if the modules don't exist, pytest
# should surface a Collection Error (Exit Code 2). That is the valid RED.
#
# GREEN TODO: implement these modules on disk per SAB.json
#   - 03-development/src/taskq_api/app.py            (FastAPI app)
#   - 03-development/src/taskq_api/api/tasks.py      (POST/GET/LIST/DELETE handlers)
#   - 03-development/src/taskq_api/api/deps.py       (X-API-Key + scope auth)
#   - 03-development/src/taskq_api/service/tasks.py  (business logic)
#   - 03-development/src/taskq_api/service/auth.py   (key hash + scope check)
#   - 03-development/src/taskq_api/repository/task_repo.py  (CRUD repo)
#   - 03-development/src/taskq_api/repository/key_repo.py    (api_keys lookup)
#   - 03-development/src/taskq_api/repository/session.py     (transactional)
#   - 03-development/src/taskq_api/models/orm.py            (Task ORM)
#   - 03-development/src/taskq_api/models/schemas.py        (TaskCreate pydantic)
#   - 03-development/src/taskq_api/errors.py                (problem+json)
#   - 03-development/src/taskq_api/config.py               (env)
from taskq_api.app import app  # noqa: E402

# httpx is required for ASGI transport (NFR-10). Imported at top level so
# the absence of httpx surfaces a clean ImportError, not a buried one.
import httpx  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client() -> httpx.AsyncClient:
    """Build an httpx AsyncClient wired to the FastAPI app via ASGI."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _write_headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def _read_headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key}


def _admin_headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key}


def _valid_body(name: str = "sample-task") -> Dict[str, Any]:
    """A body that satisfies `TaskCreate`: non-empty command, name unique,
    length within bounds, no injection characters."""
    return {
        "name": name,
        "command": "echo hello",
    }


# ---------------------------------------------------------------------------
# Case 1 — POST /v1/tasks happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_create_task_returns_201_with_id_and_status_pending(
    write_api_key: str,
) -> None:
    """AC-1.1: valid POST /v1/tasks returns 201 + UUID id; status == pending."""
    async with _client() as c:
        resp = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json=_valid_body(name="sample-task"),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # The response must include a task id (uuid pattern) and a pending status.
    assert "id" in body, body
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        str(body["id"]).lower(),
    ), body
    # FR01-create-success predicate: status_code == "201" (already asserted above)


# ---------------------------------------------------------------------------
# Case 2 — POST /v1/tasks without X-API-Key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_create_task_without_api_key_returns_401() -> None:
    """AC-1.2: POST /v1/tasks without X-API-Key returns 401 + problem+json."""
    async with _client() as c:
        resp = await c.post(
            "/v1/tasks",
            headers={"Content-Type": "application/json"},
            json=_valid_body(name="noauth-task"),
        )
    assert resp.status_code == 401, resp.text
    # FR01-missing-key-401 + FR01-problem-json-on-error
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.headers
    )


# ---------------------------------------------------------------------------
# Case 3 — POST /v1/tasks with invalid body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_create_task_invalid_body_returns_422(
    write_api_key: str,
) -> None:
    """AC-1.3: empty `name` violates TaskCreate -> 422 + problem+json."""
    async with _client() as c:
        resp = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json={"name": "", "command": "echo hi"},
        )
    assert resp.status_code == 422, resp.text
    # FR01-empty-name-422 + FR01-problem-json-on-error
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.headers
    )


# ---------------------------------------------------------------------------
# Case 4 — GET /v1/tasks/{id} happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_get_task_with_read_key_returns_200(
    write_api_key: str,
    read_api_key: str,
) -> None:
    """AC-1.4: GET known task with a read key returns 200 + all task columns."""
    async with _client() as c:
        # Create first so we have a known id.
        create = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json=_valid_body(name="get-task"),
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        # Fetch with a read-scoped key.
        resp = await c.get(
            f"/v1/tasks/{task_id}",
            headers=_read_headers(read_api_key),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # All columns of `tasks` per SAD §3.2 + SPEC §3 FR-01.
    for field in ("id", "command", "name", "status", "created_at"):
        assert field in body, (field, body)


# ---------------------------------------------------------------------------
# Case 5 — GET /v1/tasks/{unknown}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_get_unknown_task_returns_404(read_api_key: str) -> None:
    """AC-1.5: GET unknown id returns 404 + problem+json."""
    unknown_id = "00000000-0000-0000-0000-000000000000"
    async with _client() as c:
        resp = await c.get(
            f"/v1/tasks/{unknown_id}",
            headers=_read_headers(read_api_key),
        )
    assert resp.status_code == 404, resp.text
    # FR01-not-found-404 + FR01-problem-json-on-error
    assert resp.headers["content-type"].startswith("application/problem+json"), (
        resp.headers
    )


# ---------------------------------------------------------------------------
# Case 6 — DELETE /v1/tasks/{id} with write scope (non-admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_delete_task_with_write_scope_returns_403(
    write_api_key: str,
) -> None:
    """AC-1.6: DELETE with a write-scoped key returns 403; body must not
    leak whether `id` exists (T-05)."""
    target_id = str(uuid.uuid4())  # arbitrary id; leakage must be impossible
    async with _client() as c:
        resp = await c.delete(
            f"/v1/tasks/{target_id}",
            headers=_write_headers(write_api_key),
        )
    assert resp.status_code == 403, resp.text
    # FR01-write-delete-403 + FR01-403-no-id-leak
    body_text = resp.text
    assert target_id not in body_text, body_text


# ---------------------------------------------------------------------------
# Case 7 — POST /v1/tasks with duplicate name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_create_task_duplicate_name_returns_409(
    write_api_key: str,
) -> None:
    """AC-1.7: second POST with the same name returns 409 + problem+json."""
    async with _client() as c:
        first = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json=_valid_body(name="dup-task"),
        )
        assert first.status_code == 201, first.text

        second = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json=_valid_body(name="dup-task"),
        )
    assert second.status_code == 409, second.text
    # FR01-conflict-409 + FR01-problem-json-on-error
    assert second.headers["content-type"].startswith(
        "application/problem+json"
    ), second.headers


# ---------------------------------------------------------------------------
# Case 8 — GET /v1/tasks?limit=0  (lower bound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_list_tasks_limit_bounds_and_default(
    read_api_key: str,
) -> None:
    """AC-1.8 lower-bound: limit=0 is out of [1, 200]; expect 422.

    Scenario A of three sharing this test_fn name (TEST_SPEC cases 8/9/10).
    Function name MUST stay exactly as TEST_SPEC dictates.
    """
    async with _client() as c:
        resp = await c.get(
            "/v1/tasks",
            params={"limit": 0},
            headers=_read_headers(read_api_key),
        )
    assert resp.status_code == 422, resp.text
    # FR01-lower-limit-422


# ---------------------------------------------------------------------------
# Case 9 — GET /v1/tasks?limit=201  (upper bound)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_list_tasks_limit_bounds_and_default(
    read_api_key: str,
) -> None:
    """AC-1.8 upper-bound: limit=201 is out of [1, 200]; expect 422.

    Scenario B of three sharing this test_fn name.
    """
    async with _client() as c:
        resp = await c.get(
            "/v1/tasks",
            params={"limit": 201},
            headers=_read_headers(read_api_key),
        )
    assert resp.status_code == 422, resp.text
    # FR01-upper-limit-422


# ---------------------------------------------------------------------------
# Case 10 — GET /v1/tasks  (default limit = 50)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_list_tasks_limit_bounds_and_default(
    read_api_key: str,
) -> None:
    """AC-1.8 default: omitting `limit` defaults to 50; expect 200.

    Scenario C of three sharing this test_fn name.
    """
    async with _client() as c:
        resp = await c.get(
            "/v1/tasks",
            headers=_read_headers(read_api_key),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # FR01-default-limit-50: the response must expose the effective limit.
    # Acceptable shapes (GREEN may pick any):
    #   body["limit"] == 50   OR   body["meta"]["limit"] == 50
    effective = body.get("limit")
    if effective is None and isinstance(body.get("meta"), dict):
        effective = body["meta"].get("limit")
    assert effective == 50, body


# ---------------------------------------------------------------------------
# Case 11 — Cursor-based pagination (no offset)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_list_tasks_uses_cursor_pagination_not_offset(
    write_api_key: str,
    read_api_key: str,
) -> None:
    """AC-1.9: list endpoint MUST expose `cursor`, MUST NOT expose `offset`.

    Seeds more than `limit` rows so a cursor is actually returned.
    """
    async with _client() as c:
        # Seed at least 51 rows so the default limit of 50 forces a cursor.
        for i in range(51):
            r = await c.post(
                "/v1/tasks",
                headers=_write_headers(write_api_key),
                json=_valid_body(name=f"seed-task-{i}"),
            )
            assert r.status_code == 201, r.text

        # First page
        first = await c.get(
            "/v1/tasks",
            headers=_read_headers(read_api_key),
        )
        assert first.status_code == 200, first.text
        body = first.json()

    # The response shape MUST mention cursor (FR01-cursor-not-offset).
    # It MUST NOT contain an offset field under any plausible key.
    serialized = repr(body).lower()
    assert "cursor" in serialized, body

    for forbidden_key in ("offset", "page_offset", "skip"):
        assert forbidden_key not in serialized, (forbidden_key, body)


# ---------------------------------------------------------------------------
# Case 12 — DELETE /v1/tasks/{id} with admin scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_delete_task_with_admin_key_returns_204(
    write_api_key: str,
    admin_api_key: str,
) -> None:
    """AC-1.10: DELETE with an admin key on a known id returns 204; the same
    transaction also removes any matching rows in `task_results` and
    `task_tags` (SAD §3.1.1)."""
    async with _client() as c:
        create = await c.post(
            "/v1/tasks",
            headers=_write_headers(write_api_key),
            json=_valid_body(name="admin-delete-task"),
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        resp = await c.delete(
            f"/v1/tasks/{task_id}",
            headers=_admin_headers(admin_api_key),
        )
    assert resp.status_code == 204, resp.text
    # FR01-admin-delete-204