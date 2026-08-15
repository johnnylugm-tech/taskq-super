"""problem+json error helpers — RFC 7807 shape.

[FR-01] — TaskQError + problem() for FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7.
[FR-05] — AC-5.1 / AC-5.2: problem() carries optional `headers` so the
rate-limit gate can attach `Retry-After: <seconds>` to a 429 response
without bypassing the problem+json handler.
[FR-10] — RFC 7807 contract: every non-2xx response carries
`Content-Type: application/problem+json` with a body containing exactly
the six fields `type, title, status, detail, instance, correlation_id`
(AC-10.1 / AC-10.2). The `correlation_id` is also set on the
`X-Correlation-Id` response header (AC-10.4) AND emitted in a structured
server log line as the join key between client traces and server-side
audit (AC-10.5). The 500 fallback never echoes the exception text into
the body so stack / SQL / path / traceback / query fragments can never
surface (AC-10.3 / AC-10.7).

Citations:
- taskq_api.errors:TaskQError & problem()    FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7
- taskq_api.errors:problem()                 extended with `headers=` per FR-05 AC-5.1
- taskq_api.errors:problem_json_response     FR-10 AC-10.1 / AC-10.2 / AC-10.3 / AC-10.4 / AC-10.5
- taskq_api.errors:render_problem            FR-10 helper for non-exception paths (e.g. /readyz)
- taskq_api.errors:install_exception_handlers  FR-10 wires RFC 7807 envelope + StarletteHTTPException
- taskq_api.errors:_generate_correlation_id    FR-10 server-generated UUID
- taskq_api.errors:_build_problem_body       FR-10 six-field contract
- taskq_api.errors:_emit_log_line            FR-10 AC-10.5 log join key
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# Structured logger — emitted lines carry the `correlation_id` join key
# required by FR-10 AC-10.5.
_log = logging.getLogger("taskq_api")


# The fixed set of body fields the problem+json envelope must carry.
# FR-10 AC-10.2 — no more, no fewer.
PROBLEM_BODY_FIELDS: tuple[str, ...] = (
    "type",
    "title",
    "status",
    "detail",
    "instance",
    "correlation_id",
)


# Canonical (status -> title) / (status -> generic detail) maps used by
# the StarletteHTTPException and 500 fallback paths so they emit
# well-formed envelopes without leaking internals.
_STATUS_TITLE: Dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


_STATUS_DETAIL: Dict[int, str] = {
    400: "bad request",
    401: "unauthorized",
    403: "forbidden",
    404: "resource not found",
    405: "method not allowed",
    409: "conflict",
    422: "validation failed",
    429: "rate limit exceeded",
    500: "internal server error",
    503: "service unavailable",
}


class TaskQError(Exception):
    """Raised by service/handler code to surface a structured problem+json."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        # [FR-01] [FR-05] AC-5.1 — headers round-trip into the JSONResponse.
        self.status = status
        self.title = title
        self.detail = detail
        self.headers: Dict[str, str] = dict(headers or {})


def problem(
    status: int,
    title: str,
    detail: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> TaskQError:
    """Build a TaskQError that the global handler renders as problem+json."""
    return TaskQError(status=status, title=title, detail=detail, headers=headers)


def _generate_correlation_id(request: Request) -> str:
    """Return the correlation_id for the current request.

    [FR-10] — AC-10.4. Re-uses the inbound `X-Correlation-Id` header
    when present; otherwise mints a fresh UUID4. The same value is set
    on the response header AND in the response body AND emitted in the
    server log line so the client trace and the server audit share one
    join key.

    Defensive ``isinstance(..., str)`` guard so a stub request (e.g. a
    ``MagicMock`` passed by an internal-coverage test that exercises
    the global handler without spinning up a full ASGI cycle) cannot
    smuggle a non-string sentinel past the truthy check and break
    ``json.dumps`` later.
    """
    inbound = request.headers.get("X-Correlation-Id") or request.headers.get(
        "x-correlation-id"
    )
    if isinstance(inbound, str):
        cleaned = inbound.strip()
        if cleaned:
            return cleaned
    return str(uuid.uuid4())


def _build_problem_body(
    *,
    status: int,
    title: str,
    detail: str,
    instance: str,
    correlation_id: str,
) -> Dict[str, object]:
    """Construct the canonical six-field RFC 7807 body (FR-10 AC-10.2)."""
    return {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "correlation_id": correlation_id,
    }


def _emit_log_line(
    *,
    status: int,
    correlation_id: str,
    instance: str,
    title: str,
) -> None:
    """Emit one structured log line carrying the correlation_id join key.

    [FR-10] — AC-10.5. The `taskq_api` logger at WARNING for 5xx and
    INFO for 4xx so the line is captured by both pytest's caplog
    (`set_level(logging.INFO, logger="taskq_api")`) and the production
    logging configuration without raising the level globally.
    """
    level = logging.WARNING if status >= 500 else logging.INFO
    _log.log(
        level,
        "request_outcome status=%d correlation_id=%s instance=%s title=%s",
        status,
        correlation_id,
        instance,
        title,
    )


async def problem_json_response(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Render an exception as application/problem+json (RFC 7807).

    [FR-10] — AC-10.1 / AC-10.2 / AC-10.3 / AC-10.4 / AC-10.5. The
    response body carries exactly the six allowed fields, the
    `X-Correlation-Id` header mirrors the body's `correlation_id`, and
    one structured log line is emitted carrying the same value.
    """
    correlation_id = _generate_correlation_id(request)
    raw_instance = str(request.url.path) if getattr(request, "url", None) else ""

    # Pick the canonical (status, title, detail) triple for this exception
    # type. The 500 fallback never echoes the exception text into the body
    # so stack / SQL / path / traceback / query fragments can never
    # surface (AC-10.3 / AC-10.7).
    extra_headers: Dict[str, str] = {}
    if isinstance(exc, TaskQError):
        status = exc.status
        title = exc.title
        detail = exc.detail
        extra_headers.update(exc.headers or {})
    elif isinstance(exc, RequestValidationError):
        status = 422
        title = "Validation Error"
        detail = "request validation failed"
    elif isinstance(exc, StarletteHTTPException):
        status = int(exc.status_code)
        title = _STATUS_TITLE.get(status, "Error")
        if exc.detail is not None and str(exc.detail):
            detail = str(exc.detail)
        else:
            detail = _STATUS_DETAIL.get(status, "error")
    else:
        # Generic 500 — DO NOT leak the exception text into the body.
        status = 500
        title = "Internal Server Error"
        detail = "internal server error"

    # [FR-04] / NFR-02 / T-05 — 403 (insufficient scope) must NOT echo the
    # request URL in the body, because the path usually carries the
    # resource id (`/v1/tasks/{task_id}`) the caller is not authorised to
    # know about. The `instance` field is still present in the body so
    # the FR-10 AC-10.2 six-field contract holds, but its value is
    # scrubbed — security-sensitive statuses get an empty `instance`.
    if status == 403:
        instance = ""
    else:
        instance = raw_instance

    body = _build_problem_body(
        status=status,
        title=title,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
    )
    response_headers: Dict[str, str] = {
        "X-Correlation-Id": correlation_id,
        **extra_headers,
    }
    _emit_log_line(
        status=status,
        correlation_id=correlation_id,
        instance=instance,
        title=title,
    )
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=response_headers,
    )


def render_problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """Render a problem+json response directly (non-exception path).

    [FR-10] — convenience for endpoints that want to surface a
    structured failure (e.g. ``/readyz`` returning 503) without raising
    an exception. Generates a correlation_id from the request, sets the
    ``X-Correlation-Id`` header, and emits the standard log line.
    """
    correlation_id = _generate_correlation_id(request)
    instance = str(request.url.path) if request.url else ""
    body = _build_problem_body(
        status=status,
        title=title,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
    )
    response_headers: Dict[str, str] = {
        "X-Correlation-Id": correlation_id,
        **(extra_headers or {}),
    }
    _emit_log_line(
        status=status,
        correlation_id=correlation_id,
        instance=instance,
        title=title,
    )
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=response_headers,
    )


def install_exception_handlers(app) -> None:  # noqa: ANN001
    """Register problem+json handlers on a FastAPI app.

    [FR-10] — wires the RFC 7807 envelope for every exception type
    FastAPI / Starlette can surface: ``TaskQError`` (service layer),
    ``RequestValidationError`` (422 body validation),
    ``StarletteHTTPException`` (404 on unmatched routes, 405 on
    disallowed methods, etc.), and the catch-all ``Exception`
    (unhandled 500s).

    Citations:
    - taskq_api.errors:install_exception_handlers  FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7
    / FR-10 AC-10.1 / AC-10.2 / AC-10.3 / AC-10.4 / AC-10.5
    """
    app.add_exception_handler(TaskQError, problem_json_response)
    app.add_exception_handler(RequestValidationError, problem_json_response)
    app.add_exception_handler(StarletteHTTPException, problem_json_response)
    app.add_exception_handler(Exception, problem_json_response)


__all__: list[str] = [
    "TaskQError",
    "problem",
    "install_exception_handlers",
    "problem_json_response",
    "render_problem",
    "PROBLEM_BODY_FIELDS",
]
