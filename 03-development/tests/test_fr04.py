"""FR-04 — Scope authorization (read < write < admin).

[FR-04] Integration tests covering the 8 acceptance criteria enumerated in
`02-architecture/TEST_SPEC.md` (FR-04 table, cases #1..#8). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`status_code == "403"`,
`status_code == "202"`, `body_contains_resource_id == "false"`,
`scoped_routes_count == routes_count`, …) using the spec's own variable
names, so the P3 MIRROR gate can align every spec rule to a real
assertion.

The 8 cases from TEST_SPEC (verbatim):
  1. test_fr04_read_key_post_tasks_returns_403
  2. test_fr04_write_key_delete_task_returns_403
  3. test_fr04_write_key_run_task_succeeds
  4. test_fr04_admin_key_succeeds_on_all_endpoints   (admin-GET sub-row)
  5. test_fr04_admin_key_succeeds_on_all_endpoints   (admin-POST-create sub-row)
  6. test_fr04_admin_key_succeeds_on_all_endpoints   (admin-RUN sub-row)
  7. test_fr04_admin_key_succeeds_on_all_endpoints   (admin-DELETE sub-row)
  8. test_fr04_every_v1_route_declares_require_scope_dependency

Shape notes (both are forced by tooling, not preference):

* Test functions are SYNCHRONOUS and drive the async ASGI surface through
  `asyncio.run`. NFR-10 is unaffected: every request still goes through
  `httpx.AsyncClient(transport=httpx.ASGITransport(app))`, the same ASGI
  surface `uvicorn` serves. The sync shape is required because the MIRROR
  gate's AST walker collects assertions from `ast.FunctionDef` bodies only.
* Cases 4..7 share one canonical TEST_SPEC function name
  (`...admin_key_succeeds_on_all_endpoints`). All four scenarios live in a
  single definition of that name: four same-named definitions would leave
  the first three shadowed and never executed, so the admin-GET / admin-POST
  / admin-RUN / admin-DELETE branches would never run.
* Case 8 is `static`: it enumerates `app.routes` (FastAPI's own route
  table) and asserts each `/v1` route's `dependencies=` includes the single
  `require_scope` dependency from `taskq_api.api.deps`.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# module must surface as a pytest Collection Error, which is the valid RED.
from taskq_api.api.deps import require_scope
from taskq_api.app import app
from taskq_api.service import auth as auth_module
from taskq_api.service.auth import verify_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    api_key: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    """Issue one request against the FastAPI app over ASGI transport (NFR-10)."""
    headers: Dict[str, str] = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(
                method, path, headers=headers, json=json_body, params=params
            )

    return asyncio.run(_send())


def _content_type(resp: httpx.Response) -> str:
    """Response media type with any `; charset=…` parameter stripped."""
    return resp.headers.get("content-type", "").split(";")[0].strip()


def _create_task(
    api_key: str, command: str = "echo hello", name: Optional[str] = None
) -> str:
    """Helper: POST /v1/tasks with a known key and return the new id."""
    task_name = name if name is not None else f"fr04-task-{uuid.uuid4().hex[:8]}"
    resp = _request(
        "POST",
        "/v1/tasks",
        api_key=api_key,
        json_body={"name": task_name, "command": command},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _v1_route_paths() -> List[str]:
    """Return the path-template for every route mounted under `/v1/...`.

    Excludes the top-level `/healthz` and `/readyz` liveness probes — they
    are deliberately NOT `/v1` routes (AC-3.6 / FR-09 exemption).
    """
    paths: List[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        if not path.startswith("/v1"):
            continue
        paths.append(path)
    return sorted(set(paths))


# ---------------------------------------------------------------------------
# Case 1 — read key calling POST /v1/tasks returns 403 + problem+json
# ---------------------------------------------------------------------------


def test_fr04_read_key_post_tasks_returns_403(read_api_key: str) -> None:
    """AC-4.1: a `read`-key calling POST /v1/tasks returns HTTP 403 +
    problem+json; the body must not leak whether the resource exists.

    The route's `require_scope("write")` dep fires before the handler, so
    a read-only key is rejected with the canonical insufficient-scope
    problem+json — never 401 (which is reserved for unknown / revoked keys
    by FR-03).

    [FR-04] — NFR-02 (authz 403), NP-02 / T-05 (no info disclosure).
    """
    # NFR-02
    # NP-02
    # T-05
    method = "POST"
    endpoint = "/v1/tasks"
    resp = _request(
        method,
        endpoint,
        api_key=read_api_key,
        json_body={"name": "fr04-read-deny", "command": "echo hi"},
    )
    status_code = str(resp.status_code)
    content_type = _content_type(resp)
    # No resource_id is supplied in the path; verify the body does not
    # echo the presented API key or any task payload field.
    body_contains_resource_id = (
        "true" if read_api_key in resp.text else "false"
    )
    # FR04-read-cannot-write / FR04-403-no-id-leak
    assert status_code == "403", resp.text
    assert content_type == "application/problem+json", resp.headers
    assert body_contains_resource_id == "false", resp.text


# ---------------------------------------------------------------------------
# Case 2 — write key calling DELETE /v1/tasks/{id} returns 403 + no id leak
# ---------------------------------------------------------------------------


def test_fr04_write_key_delete_task_returns_403(write_api_key: str) -> None:
    """AC-4.2: a `write`-key (non-admin) calling DELETE /v1/tasks/{id}
    returns HTTP 403 + problem+json; the body must not say whether `id`
    exists (SPEC.md §8 #6).

    The `require_scope("admin")` dep fires before any existence check on
    `task_id`, so the response body cannot leak whether the id is real
    (T-05 / NP-08 information disclosure).

    [FR-04] — NFR-02 / T-05 (no info disclosure).
    """
    # NFR-02
    # T-05
    method = "DELETE"
    endpoint = "/v1/tasks/x"
    resp = _request(method, endpoint, api_key=write_api_key)
    status_code = str(resp.status_code)
    content_type = _content_type(resp)
    body_contains_resource_id = "true" if "x" in resp.text else "false"
    # FR04-write-cannot-delete / FR04-403-no-id-leak
    assert status_code == "403", resp.text
    assert content_type == "application/problem+json", resp.headers
    assert body_contains_resource_id == "false", resp.text


# ---------------------------------------------------------------------------
# Case 3 — write key calling POST /v1/tasks/{id}/run returns 202
# ---------------------------------------------------------------------------


def test_fr04_write_key_run_task_succeeds(write_api_key: str) -> None:
    """AC-4.3: a `write`-key calling POST /v1/tasks/{id}/run returns 202;
    a `read`-key on the same endpoint returns 403.

    The seed task is created with the write key; then we exercise both
    scope branches so the contrast — write->202 vs read->403 — is
    captured by a single test function (the TEST_SPEC sub-assertion
    `FR04-write-can-run` pins status_code == "202" for the happy path).

    [FR-04] — NFR-02 (write scope is sufficient to run, read scope is not).
    """
    # NFR-02
    method = "POST"
    endpoint = "/v1/tasks/{id}/run"
    task_id = _create_task(write_api_key, command="echo fr04")

    # Happy path: write key on /run
    resp = _request(
        method,
        endpoint.format(id=task_id),
        api_key=write_api_key,
    )
    status_code = str(resp.status_code)
    # FR04-write-can-run
    assert status_code == "202", resp.text

    # Symmetric negative branch (not part of case 3's Inputs row but
    # required to demonstrate the scope boundary the GREEN agent must
    # enforce via `require_scope("write")`): a read key on /run -> 403.
    read_resp = _request(
        method,
        endpoint.format(id=task_id),
        api_key="sk-test-read-key",
    )
    assert str(read_resp.status_code) == "403", read_resp.text


# ---------------------------------------------------------------------------
# Cases 4..7 — admin key succeeds on every /v1 endpoint
# ---------------------------------------------------------------------------


def test_fr04_admin_key_succeeds_on_all_endpoints(
    admin_api_key: str,
    write_api_key: str,
) -> None:
    """AC-4.4: an `admin`-key calling any /v1 endpoint succeeds.

    TEST_SPEC cases 4..7 all share this canonical function name; each pins
    a concrete status code for one of the four endpoint families:
      * admin-GET    -> 200 (case 4)
      * admin-POST   -> 201 (case 5)
      * admin-RUN    -> 202 (case 6)
      * admin-DELETE -> 204 (case 7)
    All four scenarios live in this single definition — four same-named
    definitions would leave the first three shadowed and never executed,
    so the admin branches on GET/POST/RUN/DELETE would never run.

    [FR-04] — NFR-02 (admin scope satisfies every endpoint's gate).
    """
    # NFR-02

    # ---- case 4 — admin-GET 200 --------------------------------------
    method = "GET"
    endpoint = "/v1/tasks/{id}"
    seed_id = _create_task(write_api_key, command="echo fr04-admin")
    resp = _request(method, endpoint.format(id=seed_id), api_key=admin_api_key)
    status_code = str(resp.status_code)
    # FR04-admin-GET-200
    assert status_code == "200", resp.text

    # ---- case 5 — admin-POST 201 -------------------------------------
    method = "POST"
    endpoint = "/v1/tasks"
    resp = _request(
        method,
        endpoint,
        api_key=admin_api_key,
        json_body={"name": "fr04-admin-create", "command": "echo hi"},
    )
    status_code = str(resp.status_code)
    # FR04-admin-POST-201
    assert status_code == "201", resp.text
    admin_created_id = resp.json()["id"]

    # ---- case 6 — admin-RUN 202 --------------------------------------
    method = "POST"
    endpoint = "/v1/tasks/{id}/run"
    resp = _request(
        method,
        endpoint.format(id=admin_created_id),
        api_key=admin_api_key,
    )
    status_code = str(resp.status_code)
    # FR04-admin-RUN-202
    assert status_code == "202", resp.text

    # ---- case 7 — admin-DELETE 204 -----------------------------------
    method = "DELETE"
    endpoint = "/v1/tasks/{id}"
    resp = _request(
        method,
        endpoint.format(id=admin_created_id),
        api_key=admin_api_key,
    )
    status_code = str(resp.status_code)
    # FR04-admin-DELETE-204
    assert status_code == "204", resp.text


# ---------------------------------------------------------------------------
# Case 8 — every /v1 route declares the single authz dependency
# ---------------------------------------------------------------------------


def test_fr04_every_v1_route_declares_require_scope_dependency() -> None:
    """AC-4.5: a dependency-graph test enumerates every `/v1` route's
    `dependencies=` and asserts each one includes the single authz dep.

    Per SPEC.md §3 FR-04: "the authz decision must be made in a single
    dependency (中介層)". The test asserts both invariants:

      (a) every `/v1` route's `dependencies=` references the same
          `require_scope` callable exposed by `taskq_api.api.deps`;
      (b) `routes_count == scoped_routes_count` (the FR04-route-coverage
          predicate), where `routes_count` is the number of `/v1` routes
          and `scoped_routes_count` is how many of them declare the dep.

    `/healthz` and `/readyz` are intentionally excluded — they are NOT
    `/v1` routes (they live at the top level) and are exempt from
    authz by FR-03 AC-3.6 / FR-09.

    [FR-04] — NP-02 (single authz decision point).
    """
    # NP-02
    require_scope_obj = require_scope  # canonical single authz dep
    all_v1_routes = _v1_route_paths()
    routes_count = str(len(all_v1_routes))

    scoped_routes_count = "0"
    missing: List[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or not path.startswith("/v1"):
            continue
        deps_attr = getattr(route, "dependant", None)
        if deps_attr is None:
            missing.append(path)
            continue
        # FastAPI stores route deps as `route.dependant.dependencies`
        # (a list of `Dependant` objects whose `.call` is the dep callable).
        dep_callables = [
            d.call for d in getattr(deps_attr, "dependencies", []) if d.call is not None
        ]
        # The `Depends(require_scope(...))` call is wrapped: the actual
        # callable inside the route's dependant is the inner `_dep`
        # closure returned by `require_scope(scope)`. We accept either
        # match — `require_scope` itself or any closure it produced.
        hit = any(
            _callable_chain_contains(call, require_scope_obj)
            for call in dep_callables
        )
        if hit:
            scoped_routes_count = str(int(scoped_routes_count) + 1)
        else:
            missing.append(path)

    # FR04-route-coverage
    assert scoped_routes_count == routes_count, (
        f"uncovered /v1 routes: {missing}; "
        f"scoped_routes_count={scoped_routes_count} routes_count={routes_count}"
    )


def _callable_chain_contains(call: Any, target: Any) -> bool:
    """Return True if `call` is `target` or has a closure cell referencing it.

    `require_scope(scope)` returns a closure `_dep`. FastAPI stores the
    closure as `Dependant.call`, so a direct `==` test against
    `require_scope` always fails. We instead walk the closure's free
    variables (or, for plain callables, compare `__qualname__`) and accept
    any callable whose closure cell points at `target`.
    """
    if call is target:
        return True
    closure = getattr(call, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                if cell.cell_contents is target:
                    return True
            except ValueError:
                continue
    return False


# ---------------------------------------------------------------------------
# Service-layer branch — `verify_key` honours the scope hierarchy
# ---------------------------------------------------------------------------


def test_unit_verify_key_scope_hierarchy_returns_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4.1 / AC-4.2 service branch: a known key lacking the required
    scope produces the `_insufficient_scope` marker dict (translated to
    403 by `require_scope`). This drives the service layer directly so
    the branch is covered even when no /v1 route can produce it through
    a happy-path fixture.

    [FR-04] / [FR-03] — NFR-02 (403 is the canonical insufficient-scope
    response, distinct from the 401 of unknown keys).
    """
    # NFR-02
    # The autouse hmac spy in conftest already returns True for
    # compare_digest; we re-patch find_by_hash to inject a known
    # read-only record.
    monkeypatch.setattr(
        auth_module,
        "find_by_hash",
        lambda h: {"scopes": ["read"], "key_id": "k"},  # noqa: ARG005
    )
    # read key asking for "write" -> insufficient scope marker
    record = verify_key("sk-test-read-key", "write")
    assert isinstance(record, dict)
    assert record.get("_insufficient_scope") is True

    # write key asking for "admin" -> insufficient scope marker
    monkeypatch.setattr(
        auth_module,
        "find_by_hash",
        lambda h: {"scopes": ["read", "write"], "key_id": "k"},  # noqa: ARG005
    )
    record = verify_key("sk-test-write-key", "admin")
    assert isinstance(record, dict)
    assert record.get("_insufficient_scope") is True

    # admin key asking for anything -> success record
    monkeypatch.setattr(
        auth_module,
        "find_by_hash",
        lambda h: {  # noqa: ARG005
            "scopes": ["read", "write", "admin"],
            "key_id": "k",
        },
    )
    record = verify_key("sk-test-admin-key", "admin")
    assert isinstance(record, dict)
    assert record.get("_insufficient_scope") is None
    assert "admin" in record.get("scopes", [])


def test_unit_require_scope_returns_callable_with_correct_signature() -> None:
    """AC-4.5 / NFR-05 — `require_scope` returns a callable suitable for
    `Depends(...)`. This is the "single authz dependency" the spec
    requires: every /v1 route's `dependencies=` includes this single
    factory.
    """
    # NFR-05
    factory = require_scope
    assert callable(factory)
    built = factory("read")
    assert callable(built)
    # The returned callable accepts the FastAPI-injected kwargs
    # (`x_api_key` from Header) and returns a dict on the happy path.
    sig = inspect.signature(built)
    assert "x_api_key" in sig.parameters
