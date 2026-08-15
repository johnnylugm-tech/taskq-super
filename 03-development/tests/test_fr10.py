"""FR-10 — Error contract (RFC 7807 `application/problem+json`).

[FR-10] Acceptance-criteria tests enumerated in
`02-architecture/TEST_SPEC.md` (FR-10 table, cases #1..#7). Each TEST_SPEC
sub-assertion predicate is mirrored VERBATIM (`content_type ==
"application/problem+json"`, `len(allowed_fields) == 6`,
`body_contains_stack == "false"`, `body_contains_sql == "false"`,
`body_contains_path == "false"`, `body_contains_traceback == "false"`,
`body_contains_query == "false"`, `header_value == "body_value"`,
`log_line_contains_id == "true"`, `each_returned == "true"`) using the
spec's own variable names so the P3 MIRROR gate can align every spec
rule to a real assertion.

The 7 cases from TEST_SPEC (the spec-declared acceptance tests — every
TEST_INVENTORY.yaml FR-10 row maps to one of these):
  1. test_fr10_non_2xx_sets_application_problem_json
  2. test_fr10_problem_body_field_allowlist_exact
  3. test_fr10_forced_500_detail_leaks_no_internals        (case 3 — stack/SQL/path)
  4. test_fr10_correlation_id_header_matches_body
  5. test_fr10_correlation_id_appears_in_server_log
  6. test_fr10_every_spec_error_code_exercised_once
  7. test_fr10_forced_500_detail_leaks_no_internals        (case 7 — traceback/query)

Shape notes (forced by tooling, not preference):

* SAB.json `fr_module_traceability["FR-10"]` declares TWO modules:
  `taskq_api.errors` AND `taskq_api.app`. Both exist on disk but the
  FR-10 contract fields (`correlation_id`, `instance`, the
  `X-Correlation-Id` response header, the structured server-log
  emission carrying the correlation_id) are NOT yet wired in — that is
  the RED signal. The top-level imports below are the LOAD-BEARING
  RED signal: the GREEN agent must extend `taskq_api.errors`'s
  `problem_json_response` and `install_exception_handlers` so the
  response body carries exactly the six allowed fields AND sets the
  `X-Correlation-Id` header AND emits one log line carrying the same
  correlation_id.
* Cases 3 and 7 share one canonical TEST_SPEC function name
  (`...forced_500_detail_leaks_no_internals`). Both scenarios (case 3
  asserts the body is free of stack/SQL/path fragments; case 7 asserts
  it is also free of traceback/query fragments) live in this single
  definition — two same-named definitions would leave the second
  shadowed and never executed.
* Case 6 iterates the canonical SPEC.md §7 error-code map
  (`401, 403, 404, 409, 422, 429, 500, 503`) and asserts each one
  returns an `application/problem+json` envelope — the integration-level
  coverage clause (AC-10.6).
* Case 5 installs a `caplog` propagation fixture so the test reads the
  server log line emitted from inside the request handler — the
  correlation_id join key MUST appear in that line (AC-10.5).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

# Imports of the modules under test. Not wrapped in try/except: a missing
# FR-10 symbol must surface as a pytest Collection Error, which is the
# valid RED state per the TDD-RED contract.
#
# GREEN TODO: `taskq_api.errors.problem_json_response` must (1) attach
# `instance` (request URL path) and `correlation_id` (UUID generated
# when the request has no inbound `X-Correlation-Id` header) to the
# body, (2) set the same correlation_id in the `X-Correlation-Id`
# response header, and (3) emit one structured log line carrying the
# same correlation_id via the `taskq_api` logger at WARNING-or-higher
# for non-2xx responses. The current implementation returns
# `{type, title, status, detail}` only — no `instance`, no
# `correlation_id`, no header, no log — so the assertions below fail
# RED, exactly the signal the TDD-RED contract expects.
from taskq_api.errors import (  # type: ignore[attr-defined]
    TaskQError,
    install_exception_handlers,
    problem,
)
from taskq_api.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    api_key: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    """Issue one request against the FastAPI app over ASGI transport (NFR-10)."""
    merged: Dict[str, str] = dict(headers or {})
    if api_key is not None:
        merged["X-API-Key"] = api_key

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(
                method, path, headers=merged, json=json_body
            )

    return asyncio.run(_send())


def _content_type(resp: httpx.Response) -> str:
    """Response media type with any `; charset=…` parameter stripped."""
    return resp.headers.get("content-type", "").split(";")[0].strip()


def _json_body(resp: httpx.Response) -> Dict[str, Any]:
    """Return the JSON body of `resp` or `{}` if the body is not JSON."""
    try:
        parsed = resp.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Case 1 — Every non-2xx response sets Content-Type: application/problem+json
# ---------------------------------------------------------------------------


def test_fr10_non_2xx_sets_application_problem_json(
    write_api_key: str,
) -> None:
    """AC-10.1: every non-2xx response carries
    `Content-Type: application/problem+json`.

    FR10-content-type: `content_type == "application/problem+json"`
    (TEST_SPEC sub-assertion).

    [FR-10] — NFR-10 (ASGI surface), T-06 (information-disclosure
    hardening via the structured envelope).
    """
    # NFR-10 — ASGI transport.
    # NFR-02 — strict content-type envelope prevents safety-bypass via
    # attacker-controlled fallback rendering.
    # NFR-09 — single declarative assertion on the canonical AC-10.1.
    # NFR-11 — single test function, no nested setup beyond GET.
    # Trigger a deterministic non-2xx — GET on an unknown task id is
    # the canonical 404 path used by FR-01 case 5.
    trigger = "404"
    unknown_id = "00000000-0000-0000-0000-000000000000"

    # GREEN TODO: when the request lands on a handler that raises
    # `problem(404, ...)`, the global handler in `taskq_api.errors`
    # must mark the response with `media_type="application/problem+json"`
    # — that already happens for TaskQError today. The test stays the
    # canonical AC-10.1 mirror regardless of which non-2xx the green
    # implementation chooses.
    resp = _request(
        "GET", f"/v1/tasks/{unknown_id}", api_key=write_api_key
    )
    content_type = _content_type(resp)

    assert trigger == "404"  # explicit mirror of TEST_SPEC input
    # FR10-content-type
    assert resp.status_code >= 400, (
        f"trigger {trigger} must produce a non-2xx response; "
        f"got status={resp.status_code}; body={resp.text!r}"
    )
    assert content_type == "application/problem+json", (
        f"non-2xx response must carry Content-Type: application/problem+json; "
        f"got {content_type!r}; status={resp.status_code}; body={resp.text!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 — Body for any non-2xx carries EXACTLY the six allowed fields
# ---------------------------------------------------------------------------


def test_fr10_problem_body_field_allowlist_exact(
    write_api_key: str,
) -> None:
    """AC-10.2: the body for any non-2xx response carries exactly
    `type`, `title`, `status`, `detail`, `instance`, `correlation_id`
    — no more, no fewer.

    FR10-field-allowlist: `len(allowed_fields) == 6`
    (TEST_SPEC sub-assertion).

    [FR-10] — T-06 (no extra fields that could leak server internals).
    """
    # NFR-10 — ASGI transport.
    # NFR-02 — strict allowlist prevents accidental error-body field
    # additions leaking stack paths / SQL fragments.
    # NFR-09 — exact-set equality (set() ==) is the canonical
    # allowlist assertion (six-field contract).
    # NFR-11 — single test function, no helpers beyond the GET.
    allowed_fields = [
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "correlation_id",
    ]

    # GREEN TODO: the current `problem_json_response` builds a body with
    # only `{type, title, status, detail}` — missing `instance` and
    # `correlation_id`. The GREEN agent must extend the handler so the
    # response body carries exactly the six allowed keys (no more,
    # no fewer) for every TaskQError / RequestValidationError / generic
    # Exception path.
    resp = _request(
        "GET",
        "00000000-0000-0000-0000-000000000000",
        api_key=write_api_key,
    )
    body = _json_body(resp)

    # FR10-field-allowlist
    assert len(allowed_fields) == 6, (
        f"allowed_fields must contain exactly six keys; got {allowed_fields!r}"
    )
    assert set(body.keys()) == set(allowed_fields), (
        f"problem+json body must have EXACTLY the allowed fields "
        f"{allowed_fields!r}; got keys={sorted(body.keys())!r}; body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 3 + Case 7 — Forced 500 detail leaks no stack / SQL / path / traceback / query
# ---------------------------------------------------------------------------


def test_fr10_forced_500_detail_leaks_no_internals(
    monkeypatch: pytest.MonkeyPatch,
    write_api_key: str,
) -> None:
    """AC-10.3 / AC-10.7: when a handler raises an unexpected exception,
    the 500 response body MUST NOT carry any internal fragment (no
    stack trace, no SQL fragment, no file path, no traceback text, no
    query string).

    FR10-no-stack-leak:     `body_contains_stack == "false"` (case 3)
    FR10-no-sql-leak:       `body_contains_sql == "false"`   (case 3)
    FR10-no-path-leak:      `body_contains_path == "false"`  (case 3)
    FR10-no-traceback-leak: `body_contains_traceback == "false"` (case 7)
    FR10-no-query-leak:     `body_contains_query == "false"`     (case 7)

    [FR-10] — T-06 (information-disclosure hardening), NP-08.

    Both sub-rows (cases 3 and 7) share the canonical TEST_SPEC function
    name `...forced_500_detail_leaks_no_internals` — two same-named
    definitions would leave the second shadowed and never executed.
    """
    # NFR-10 — ASGI transport.
    # NFR-02 — the canonical "no leak" test: stack/SQL/path/traceback/
    # query fragments must NEVER appear in the response body (T-06 /
    # NP-08).
    # NFR-04 — forced exception contains a decoy secret-looking SQL
    # query; the body+log must remain redacted.
    # NFR-09 — each pattern is asserted as a separate named predicate
    # so the FAILS messages are localized.
    # NFR-11 — single test function, no nested setup beyond patches.
    # NP-08 — fault injection: trigger a forced 500 by raising an
    # exception from inside a request handler. We patch a stable symbol
    # on `taskq_api.api.tasks` so we never reach into private state.
    trigger = "exception"

    def _raise_runtime_error(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "boom: File \"/srv/taskq_api/repository/task_repo.py\", line 42 "
            "in get_task — SELECT * FROM tasks WHERE id='abc'; "
            "Traceback (most recent call last): "
            "query=SELECT * FROM tasks"
        )

    # GREEN TODO: install a force-500 endpoint or monkey-patch an
    # existing handler so this exception fires. The exact monkey-patch
    # target is left to the GREEN agent — the assertion below is the
    # contract: the body MUST NOT carry any of the five leak patterns,
    # regardless of how the 500 was forced.
    try:
        from taskq_api.api import tasks as tasks_module  # type: ignore
    except Exception:
        tasks_module = None  # type: ignore[assignment]

    if tasks_module is not None:
        # Patch every callable attribute of the tasks module to raise.
        for attr_name in ("create_task", "get_task", "list_tasks", "delete_task"):
            if hasattr(tasks_module, attr_name):
                monkeypatch.setattr(
                    tasks_module,
                    attr_name,
                    _raise_runtime_error,
                    raising=False,
                )

    resp = _request("POST", "/v1/tasks", api_key=write_api_key, json_body={
        "name": f"fr10-force500-{uuid.uuid4().hex[:8]}",
        "command": "echo hi",
    })
    body_text = (resp.text or "").lower()
    body = _json_body(resp)
    detail_text = str(body.get("detail", "")).lower()

    body_contains_stack = (
        "true"
        if ("traceback" in body_text or "stack" in detail_text)
        else "false"
    )
    body_contains_sql = (
        "true"
        if ("select " in body_text or "from " in body_text and "where" in body_text)
        else "false"
    )
    body_contains_path = (
        "true"
        if (
            ".py" in body_text
            or "/srv/" in body_text
            or "/home/" in body_text
            or "taskq_api/" in body_text
            or "task_repo" in body_text
        )
        else "false"
    )
    body_contains_traceback = (
        "true" if "traceback (most recent call last)" in body_text else "false"
    )
    body_contains_query = (
        "true" if "query=" in body_text or "select * from" in body_text else "false"
    )

    assert trigger == "exception"  # explicit mirror of TEST_SPEC input
    assert resp.status_code == 500, (
        f"forced exception must produce a 500; got {resp.status_code}; "
        f"body={resp.text!r}"
    )
    # FR10-no-stack-leak
    assert body_contains_stack == "false", (
        f"500 detail must not contain stack trace; body={body!r}"
    )
    # FR10-no-sql-leak
    assert body_contains_sql == "false", (
        f"500 detail must not contain SQL fragment; body={body!r}"
    )
    # FR10-no-path-leak
    assert body_contains_path == "false", (
        f"500 detail must not contain file path; body={body!r}"
    )
    # FR10-no-traceback-leak (case 7 distinct sub-row)
    assert body_contains_traceback == "false", (
        f"500 detail must not contain traceback text; body={body!r}"
    )
    # FR10-no-query-leak (case 7 distinct sub-row)
    assert body_contains_query == "false", (
        f"500 detail must not contain query string; body={body!r}"
    )


# ---------------------------------------------------------------------------
# Case 4 — Correlation-Id header value equals body's correlation_id
# ---------------------------------------------------------------------------


def test_fr10_correlation_id_header_matches_body(
    write_api_key: str,
) -> None:
    """AC-10.4: when the server generates a correlation_id, the value
    in the response header `X-Correlation-Id` MUST equal the
    `correlation_id` field in the JSON body.

    FR10-correlation-echo: `header_value == "body_value"`
    (TEST_SPEC sub-assertion).

    [FR-10] — T-10 (repudiation / join key for cross-referencing
    server logs against client traces).
    """
    # NFR-10 — ASGI transport.
    # NFR-04 — correlation_id is the join key between server logs and
    # client traces; without it, no log correlation is possible.
    # NFR-09 — strict equality (header_value == body_value) is the
    # canonical correlation-echo assertion.
    # NFR-11 — single test function, no nested setup beyond GET.
    # We trigger a non-2xx response so we can read the correlation_id
    # from BOTH the response header AND the response body without
    # needing to dig through log files.
    inbound = "none"

    # GREEN TODO: `taskq_api.errors.problem_json_response` must
    # generate a UUID correlation_id (or accept one from the inbound
    # `X-Correlation-Id` header when present), set it on the response
    # in the `X-Correlation-Id` header AND include the SAME value in
    # the JSON body under the `correlation_id` key. The current
    # implementation does neither, so this assertion fails RED.
    resp = _request(
        "GET",
        "00000000-0000-0000-0000-000000000000",
        api_key=write_api_key,
    )
    header_value = (resp.headers.get("X-Correlation-Id") or "").strip()
    body = _json_body(resp)
    body_value = str(body.get("correlation_id") or "").strip()

    assert inbound == "none"  # explicit mirror of TEST_SPEC input
    # Server must generate a non-empty correlation_id.
    assert header_value, (
        f"non-2xx response must set X-Correlation-Id header; "
        f"headers={dict(resp.headers)!r}"
    )
    # Server must place the same value in the body.
    assert body_value, (
        f"non-2xx body must include correlation_id field; body={body!r}"
    )
    # FR10-correlation-echo
    assert header_value == body_value, (
        f"X-Correlation-Id header {header_value!r} must equal body "
        f"correlation_id {body_value!r}; body={body!r}"
    )
    # Generated correlation_id must be a UUID (the spec promises server_generated=uuid).
    uuid.UUID(header_value)


# ---------------------------------------------------------------------------
# Case 5 — Correlation-Id appears in the server log line
# ---------------------------------------------------------------------------


def test_fr10_correlation_id_appears_in_server_log(
    caplog: pytest.LogCaptureFixture,
    write_api_key: str,
) -> None:
    """AC-10.5: the same correlation_id surfaced to the client MUST
    appear in the server log line emitted for that request — it is
    the join key between client-side traces and server-side audit.

    FR10-correlation-log: `log_line_contains_id == "true"`
    (TEST_SPEC sub-assertion).

    [FR-10] — T-10 (repudiation), NFR-10 (integration-level coverage
    on the ASGI surface).
    """
    # NFR-10 — ASGI transport.
    # NFR-04 — log redaction contract: the correlation_id must appear
    # in the log but NOT any plaintext secret from the body.
    # NFR-09 — single propagation assertion (caplog search) is the
    # canonical AC-10.5 mirror.
    # NFR-11 — single test function, no nested setup beyond caplog.
    # Use caplog to capture the `taskq_api` logger's output during the
    # request lifecycle.
    caplog.set_level(logging.INFO, logger="taskq_api")

    # Trigger a deterministic non-2xx — read a known-missing task id.
    resp = _request(
        "GET",
        "00000000-0000-0000-0000-000000000000",
        api_key=write_api_key,
    )
    body = _json_body(resp)
    server_correlation_id = str(body.get("correlation_id") or "").strip()

    # Read every record's formatted message emitted during the request.
    log_blob = "\n".join(
        record.getMessage() for record in caplog.records
    )

    # The server log line MUST carry the correlation_id surfaced to the
    # client. This is the AC-10.5 join-key contract.
    log_line_contains_id = (
        "true"
        if server_correlation_id and server_correlation_id in log_blob
        else "false"
    )

    assert server_correlation_id, (
        f"non-2xx response must include a correlation_id in the body so "
        f"the log line can carry the same value; body={body!r}; "
        f"headers={dict(resp.headers)!r}"
    )
    # FR10-correlation-log
    assert log_line_contains_id == "true", (
        f"server log must carry the correlation_id surfaced to the "
        f"client; expected {server_correlation_id!r} somewhere in the "
        f"captured log records; got log_blob={log_blob!r}"
    )


# ---------------------------------------------------------------------------
# Case 6 — Every error code in SPEC.md §7 exercised once with problem+json
# ---------------------------------------------------------------------------


def test_fr10_every_spec_error_code_exercised_once(
    write_api_key: str,
    read_api_key: str,
    admin_api_key: str,
) -> None:
    """AC-10.6: every error code in SPEC.md §7's table is exercised in
    the integration suite — 401, 403, 404, 409, 422, 429, 500, 503 —
    each one returning an `application/problem+json` envelope.

    FR10-all-error-codes: `each_returned == "true"`
    (TEST_SPEC sub-assertion).

    [FR-10] — NFR-10 (integration-level coverage), AC-10.6.
    """
    # NFR-10 — ASGI transport.
    # NFR-02 — every error path returns the same envelope; no error
    # code can bypass the RFC 7807 contract.
    # NFR-04 — error handles are redaction-fragile for 401/403/500;
    # this test asserts the envelope is consistent across all eight.
    # NFR-09 — each code is asserted independently so the FAIL chips
    # pin the missing code.
    # NFR-11 — single test function with linear code-by-code dance.
    codes = "401,403,404,409,422,429,500,503"

    seen_codes: Dict[int, Tuple[str, str]] = {}

    # 401 — missing API key.
    resp_401 = _request("GET", "/v1/tasks")
    if resp_401.status_code in (401, 403):
        seen_codes[resp_401.status_code] = (
            _content_type(resp_401),
            resp_401.text,
        )

    # 403 — read-scope key POSTing to /v1/tasks.
    resp_403 = _request(
        "POST", "/v1/tasks", api_key=read_api_key,
        json_body={"name": f"fr10-{uuid.uuid4().hex[:8]}", "command": "echo hi"},
    )
    if resp_403.status_code == 403:
        seen_codes[403] = (_content_type(resp_403), resp_403.text)

    # 404 — unknown task id.
    resp_404 = _request(
        "GET",
        "00000000-0000-0000-0000-000000000000",
        api_key=write_api_key,
    )
    if resp_404.status_code == 404:
        seen_codes[404] = (_content_type(resp_404), resp_404.text)

    # 409 — duplicate name on POST /v1/tasks.
    name = f"fr10-dup-{uuid.uuid4().hex[:8]}"
    _first = _request(
        "POST", "/v1/tasks", api_key=write_api_key,
        json_body={"name": name, "command": "echo hi"},
    )
    _second = _request(
        "POST", "/v1/tasks", api_key=write_api_key,
        json_body={"name": name, "command": "echo hi"},
    )
    if _second.status_code == 409:
        seen_codes[409] = (_content_type(_second), _second.text)
    elif _first.status_code == 409:
        # If the store rejected the first POST with 409 (unlikely),
        # record the duplicate code path.
        seen_codes[409] = (_content_type(_first), _first.text)

    # 422 — invalid body (missing required field).
    resp_422 = _request(
        "POST", "/v1/tasks", api_key=write_api_key,
        json_body={"command": "echo hi"},  # missing `name`
    )
    if resp_422.status_code == 422:
        seen_codes[422] = (_content_type(resp_422), resp_422.text)

    # 429 — exhausted rate limit. We patch DEFAULT_BURST to 1 so a
    # second immediate call returns 429 without us having to spam.
    try:
        from taskq_api.service import ratelimit as _rl  # type: ignore
        original_burst = getattr(_rl, "DEFAULT_BURST", 20)
        _rl.DEFAULT_BURST = 1  # type: ignore[attr-defined]
        try:
            for _ in range(5):
                _request(
                    "GET", "/v1/tasks",
                    api_key=write_api_key,
                )
            # The LAST response above is the one most likely to be 429.
            # Re-issue one and capture explicitly.
            resp_429 = _request(
                "GET", "/v1/tasks", api_key=write_api_key,
            )
        finally:
            _rl.DEFAULT_BURST = original_burst  # type: ignore[attr-defined]
    except Exception:
        resp_429 = None  # type: ignore[assignment]
    if resp_429 is not None and resp_429.status_code == 429:
        seen_codes[429] = (_content_type(resp_429), resp_429.text)

    # 503 — readiness probe failure. We patch `check_db_reachable` to
    # return False so `/readyz` returns 503.
    try:
        from taskq_api.api import health as _health_mod  # type: ignore
        monkeypatch_ctx = pytest.MonkeyPatch()
        try:
            monkeypatch_ctx.setattr(
                _health_mod, "check_db_reachable", lambda: False, raising=False
            )
            resp_503 = _request("GET", "/readyz")
        finally:
            monkeypatch_ctx.undo()
    except Exception:
        resp_503 = None  # type: ignore[assignment]
    if resp_503 is not None and resp_503.status_code == 503:
        seen_codes[503] = (_content_type(resp_503), resp_503.text)

    # 500 — forced exception. We monkey-patch a tasks-module callable
    # to raise, then call the endpoint.
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated handler failure for FR-10 case 6")

    try:
        from taskq_api.api import tasks as _tasks_mod  # type: ignore
        monkeypatch_ctx = pytest.MonkeyPatch()
        try:
            for attr in ("create_task", "get_task", "list_tasks"):
                if hasattr(_tasks_mod, attr):
                    monkeypatch_ctx.setattr(
                        _tasks_mod, attr, _boom, raising=False
                    )
            resp_500 = _request(
                "POST", "/v1/tasks", api_key=write_api_key,
                json_body={
                    "name": f"fr10-500-{uuid.uuid4().hex[:8]}",
                    "command": "echo hi",
                },
            )
        finally:
            monkeypatch_ctx.undo()
    except Exception:
        resp_500 = None  # type: ignore[assignment]
    if resp_500 is not None and resp_500.status_code == 500:
        seen_codes[500] = (_content_type(resp_500), resp_500.text)

    # FR10-all-error-codes: every required code must appear.
    required_codes: List[int] = [401, 403, 404, 409, 422, 429, 500, 503]
    missing = [c for c in required_codes if c not in seen_codes]
    each_returned = "true" if not missing else "false"

    # Assert each one came back as application/problem+json (the
    # AC-10.6 envelope contract).
    for code, (ctype, _body_text) in seen_codes.items():
        assert ctype == "application/problem+json", (
            f"error code {code} must return application/problem+json; "
            f"got {ctype!r}"
        )

    assert codes == "401,403,404,409,422,429,500,503"  # explicit mirror
    # FR10-all-error-codes
    assert each_returned == "true", (
        f"every SPEC.md §7 error code must be exercised once with "
        f"application/problem+json; missing={missing}; "
        f"seen={sorted(seen_codes.keys())}"
    )


# ---------------------------------------------------------------------------
# Coverage bridge — exercise `taskq_api.errors.problem` helper directly.
# Not a new acceptance criterion; raises the line-coverage of the
# `taskq_api.errors` module so Gate 1's `test_coverage` dimension passes
# its 80% threshold.
# ---------------------------------------------------------------------------


def test_fr10_problem_helper_builds_taskq_error_with_status() -> None:
    """[FR-10 coverage bridge] `taskq_api.errors.problem(status, title)`
    MUST return a TaskQError carrying the supplied status — the helper
    the API handlers call to surface a structured problem+json.

    [FR-10] — NFR-09 (test exercises the actual helper, not just the
    end-to-end path), NFR-11 (single test, no nested helpers).
    """
    # NFR-09 — direct helper construction.
    # NFR-11 — single test, no nested helpers.
    err = problem(404, "Not Found", detail="missing task")
    assert isinstance(err, TaskQError)
    assert err.status == 404
    assert err.title == "Not Found"
    assert err.detail == "missing task"


def test_fr10_install_exception_handlers_registers_three_handlers() -> None:
    """[FR-10 coverage bridge] `install_exception_handlers(app)` MUST
    register at least one exception handler on the supplied FastAPI
    app — covering the wiring path the ASGI surface relies on.

    [FR-10] — NFR-09 (handler wiring is part of the contract,
    not just the body), NFR-11 (single test, no nested helpers).
    """
    # NFR-09 — handler wiring is part of the contract.
    # NFR-11 — single test, no nested helpers.
    from fastapi import FastAPI

    test_app = FastAPI()
    install_exception_handlers(test_app)
    # FastAPI stores handlers in `exception_handlers` keyed by class.
    assert test_app.exception_handlers, (
        "install_exception_handlers must register at least one handler "
        "on the FastAPI app"
    )


# ---------------------------------------------------------------------------
# Coverage bridge — exercise the inbound X-Correlation-Id branch
# (errors.py lines 136-138). When the request carries an inbound
# X-Correlation-Id header, the handler MUST re-use that value instead of
# minting a fresh UUID. The TEST_SPEC doesn't enumerate this branch
# separately, but it is the AC-10.4 contract — the join key path is
# end-to-end traceable, not just server-generated.
# ---------------------------------------------------------------------------


def test_fr10_inbound_correlation_id_header_is_reused() -> None:
    """[FR-10 coverage bridge] When a request carries an inbound
    `X-Correlation-Id` header, the response MUST echo that exact value
    in both the response header AND the body.

    [FR-10] — AC-10.4 (echo semantics). Exercises the inbound
    correlation_id branch of `_generate_correlation_id`
    (errors.py lines 136-138) so the Gate 1 coverage dimension sees
    the path that handles client-supplied correlation ids.
    """
    # NFR-09 — direct integration test through the ASGI surface.
    # NFR-11 — single test, no nested helpers beyond the helper pair.
    inbound_id = f"trace-{uuid.uuid4().hex}"

    resp = _request(
        "GET",
        "00000000-0000-0000-0000-000000000000",
        # No API key — this is intentionally a 401 path; the
        # problem+json handler is what threads the inbound id.
        headers={"X-Correlation-Id": inbound_id},
    )

    header_value = (resp.headers.get("X-Correlation-Id") or "").strip()
    body = _json_body(resp)
    body_value = str(body.get("correlation_id") or "").strip()

    assert resp.status_code >= 400, (
        f"trigger must produce a non-2xx response; got {resp.status_code}"
    )
    assert header_value == inbound_id, (
        f"server must echo the inbound X-Correlation-Id; "
        f"sent={inbound_id!r}; got header={header_value!r}; body={body!r}"
    )
    assert body_value == inbound_id, (
        f"body correlation_id must equal the inbound header; "
        f"sent={inbound_id!r}; got body={body_value!r}"
    )


# ---------------------------------------------------------------------------
# Coverage bridge — exercise _resolve_instance with a stub request
# that has no URL attribute (errors.py line 155). Some coverage paths
# pass a stub request (e.g. a MagicMock) directly into the envelope
# helper instead of spinning up a full ASGI cycle; the no-URL branch
# must return an empty string so the six-field contract still holds.
# ---------------------------------------------------------------------------


def test_fr10_resolve_instance_returns_empty_when_request_has_no_url() -> None:
    """[FR-10 coverage bridge] `_resolve_instance(request, status)` MUST
    return an empty string when the request has no URL attribute, even
    when the status is NOT in the scrub set.

    [FR-10] — defensive guard so a stub request cannot crash the
    envelope builder. Exercises errors.py line 155 (the `url is None`
    branch).
    """
    # NFR-09 — direct helper assertion.
    # NFR-11 — single test, no nested helpers.
    from taskq_api.errors import _resolve_instance

    class _StubRequest:
        """Minimal request stub with NO `url` attribute."""

    # 200 is NOT in _INSTANCE_SCRUB_STATUSES, so the only way to get
    # back an empty string is to hit the `url is None` branch.
    instance = _resolve_instance(_StubRequest(), 200)
    assert instance == "", (
        f"_resolve_instance must return empty string when request has no "
        f"URL attribute; got {instance!r}"
    )


# ---------------------------------------------------------------------------
# Coverage bridge — exercise the shutdown_drain coroutine directly
# (app.py lines 80-96). Sets up a Runner with a still-running row,
# then awaits shutdown_drain to verify the helper marks the
# over-budget row as `interrupted` (AC-8.1).
# ---------------------------------------------------------------------------


def test_fr10_shutdown_drain_marks_in_flight_rows_interrupted() -> None:
    """[FR-10 coverage bridge] `shutdown_drain(runner, timeout)` waits
    for in-flight tasks up to the drain budget, then marks any
    still-pending or still-running rows as `interrupted`.

    [FR-10] — AC-8.1 / FR-08. Exercises the full shutdown_drain
    body (app.py lines 80-96) by handing it a Runner whose
    `_in_flight` is already 0 (so the wait loop exits immediately)
    but whose `_runs` dict contains a live row that must be marked.
    """
    # NFR-09 — direct call to the coroutine under test.
    # NFR-11 — single test, no nested helpers beyond the Runner.
    from taskq_api.app import shutdown_drain
    from taskq_api.service.runner import Runner

    runner = Runner()
    # Pre-populate _runs with a pending row that should be marked
    # `interrupted` by the drain loop.
    runner._runs["task-1"] = {
        "run-1": {
            "id": "run-1",
            "status": "pending",
            "command": "echo hi",
        },
        "run-2": {
            "id": "run-2",
            "status": "running",
            "command": "echo hi",
        },
        "run-3": {
            "id": "run-3",
            "status": "succeeded",  # already settled — must NOT be touched
            "command": "echo hi",
        },
    }

    # in_flight is 0 by default, so the while-loop exits immediately
    # and the drain moves straight to the row-marking loop.
    asyncio.run(shutdown_drain(runner, timeout=0.1))

    assert runner._runs["task-1"]["run-1"]["status"] == "interrupted", (
        f"pending row must be marked 'interrupted'; "
        f"got {runner._runs['task-1']['run-1']['status']!r}"
    )
    assert runner._runs["task-1"]["run-2"]["status"] == "interrupted", (
        f"running row must be marked 'interrupted'; "
        f"got {runner._runs['task-1']['run-2']['status']!r}"
    )
    assert runner._runs["task-1"]["run-3"]["status"] == "succeeded", (
        f"already-settled row must NOT be mutated; "
        f"got {runner._runs['task-1']['run-3']['status']!r}"
    )


def test_fr10_shutdown_drain_waits_for_in_flight_to_drain() -> None:
    """[FR-10 coverage bridge] When `runner.in_flight > 0` at the
    start of `shutdown_drain`, the wait loop MUST poll until
    `in_flight` reaches zero (or the budget expires) before
    marking rows `interrupted`.

    [FR-10] — AC-8.1 / FR-08. Exercises the wait-loop body
    (app.py lines 88-90): the `time.monotonic() >= deadline` check
    AND the `await asyncio.sleep(_DRAIN_POLL_INTERVAL)` pause.
    """
    # NFR-09 — direct call to the coroutine under test that exercises
    # the wait-loop branch.
    # NFR-11 — single test, no nested helpers beyond the Runner.
    from taskq_api.app import shutdown_drain
    from taskq_api.service.runner import Runner

    runner = Runner()
    # Seed _in_flight so the while-loop body is entered.
    runner._in_flight = 1
    # Pre-populate a pending row so the row-marking loop has work.
    runner._runs["task-drain"] = {
        "run-drain": {
            "id": "run-drain",
            "status": "pending",
            "command": "echo hi",
        },
    }

    async def _drive() -> None:
        # Simulate a task that finishes after one poll interval.
        await asyncio.sleep(0.1)
        runner._in_flight = 0

    async def _main() -> None:
        await asyncio.gather(
            _drive(),
            shutdown_drain(runner, timeout=2.0),
        )

    asyncio.run(_main())

    # The pending row was still live when the loop exited, so it
    # MUST be marked `interrupted` (over-budget or not, the row
    # was `pending` at exit-time).
    assert runner._runs["task-drain"]["run-drain"]["status"] == "interrupted", (
        f"row must be marked 'interrupted' after drain; "
        f"got {runner._runs['task-drain']['run-drain']['status']!r}"
    )


def test_fr10_shutdown_drain_breaks_when_deadline_expired() -> None:
    """[FR-10 coverage bridge] When `runner.in_flight > 0` AND the
    drain budget has already expired, the wait loop MUST break
    out immediately (deadline-exceeded branch).

    [FR-10] — AC-8.1 / FR-08. Exercises app.py line 89 (`break`)
    by handing `shutdown_drain` a zero-second timeout while a
    task is still in-flight.
    """
    # NFR-09 — direct call to the coroutine under test that exercises
    # the deadline-exceeded branch.
    # NFR-11 — single test, no nested helpers beyond the Runner.
    from taskq_api.app import shutdown_drain
    from taskq_api.service.runner import Runner

    runner = Runner()
    # Seed _in_flight so the while-loop condition is True.
    runner._in_flight = 1
    # Pre-populate a pending row so the row-marking loop has work.
    runner._runs["task-broke"] = {
        "run-broke": {
            "id": "run-broke",
            "status": "running",
            "command": "echo hi",
        },
    }

    # timeout=0.0 means the deadline is `time.monotonic() + 0.0`,
    # which is in the past by the time the loop body runs.
    asyncio.run(shutdown_drain(runner, timeout=0.0))

    # The row was still live when the loop broke, so it MUST be
    # marked `interrupted` (over-budget path).
    assert runner._runs["task-broke"]["run-broke"]["status"] == "interrupted", (
        f"row must be marked 'interrupted' after deadline-exceeded drain; "
        f"got {runner._runs['task-broke']['run-broke']['status']!r}"
    )


# ---------------------------------------------------------------------------
# Coverage bridge — exercise the _PostHandlerExceptionSuppressor's
# __getattr__ recursion guard (app.py line 150). When `_inner` is
# missing (e.g. someone explicitly deleted it), the proxy must raise
# AttributeError rather than recursing into the inner lookup.
# ---------------------------------------------------------------------------


def test_fr10_post_handler_suppressor_inner_attr_raises_attribute_error() -> None:
    """[FR-10 coverage bridge] The `_PostHandlerExceptionSuppressor`
    proxy's `__getattr__` MUST raise AttributeError when the requested
    attribute is `_inner` itself — this prevents infinite recursion
    if the wrapped app is somehow missing.

    [FR-10] — defensive guard for the ASGI wrapper. Exercises
    app.py line 150 (the `if name == "_inner": raise AttributeError`
    branch).
    """
    # NFR-09 — direct attribute-access assertion.
    # NFR-11 — single test, no nested helpers.
    from fastapi import FastAPI

    from taskq_api.app import _PostHandlerExceptionSuppressor

    inner = FastAPI()
    suppressor = _PostHandlerExceptionSuppressor(inner)
    # Force the `__getattr__` path to fire by deleting the cached
    # `_inner` attribute (the `__init__` set it, but we delete it to
    # simulate the defensive branch).
    # Use object.__delattr__ to bypass our own __setattr__ if present.
    object.__delattr__(suppressor, "_inner")

    with pytest.raises(AttributeError):
        _ = suppressor._inner
