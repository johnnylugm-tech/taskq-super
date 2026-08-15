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
- taskq_api.errors:_resolve_exception_envelope FR-10 picks (status, title, detail) per exception type
- taskq_api.errors:_envelope_response        FR-10 shared body+headers+log builder
- taskq_api.errors:_resolve_instance         FR-10 scrubs `instance` for security-sensitive statuses
- taskq_api.errors:_generate_correlation_id  FR-10 server-generated UUID
- taskq_api.errors:_build_problem_body       FR-10 six-field contract
- taskq_api.errors:_emit_log_line            FR-10 AC-10.5 log join key
"""

from __future__ import annotations

# pragma: no error-handling — RFC 7807 envelope constructor. It builds
# dicts/JSONResponse and emits one log line; `logging` never propagates
# handler failures to the caller, so nothing here can fail recoverably.

import logging
import uuid
from typing import Dict, Mapping, Optional, Tuple

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# Structured logger — emitted lines carry the `correlation_id` join key
# required by FR-10 AC-10.5.
_log = logging.getLogger("taskq_api")


# The fixed set of body fields the problem+json envelope must carry.
# FR-10 AC-10.2 — no more, no fewer.
PROBLEM_BODY_FIELDS: Tuple[str, ...] = (
    "type",
    "title",
    "status",
    "detail",
    "instance",
    "correlation_id",
)


# Default RFC 7807 `type` value when the server hasn't assigned a
# problem-type URI for a given status.
_DEFAULT_TYPE: str = "about:blank"


# Statuses for which the request URL path would itself disclose
# information the caller is not authorised to know (e.g. 403 on
# `/v1/tasks/{task_id}` leaks which resource id was probed). The
# six-field AC-10.2 body contract still holds — only the *value* of
# `instance` is cleared.
_INSTANCE_SCRUB_STATUSES: frozenset[int] = frozenset({403})


# Canonical (status -> (title, fallback detail)) map. Paired rather
# than parallel dicts so the two strings can never drift apart and every
# problem+json path uses the same wording per status.
_STATUS_ENVELOPES: Mapping[int, Tuple[str, str]] = {
    400: ("Bad Request", "bad request"),
    401: ("Unauthorized", "unauthorized"),
    403: ("Forbidden", "forbidden"),
    404: ("Not Found", "resource not found"),
    405: ("Method Not Allowed", "method not allowed"),
    409: ("Conflict", "conflict"),
    422: ("Unprocessable Entity", "validation failed"),
    429: ("Too Many Requests", "rate limit exceeded"),
    500: ("Internal Server Error", "internal server error"),
    503: ("Service Unavailable", "service unavailable"),
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


def _resolve_instance(request: Request, status: int) -> str:
    """Return the safe value for the `instance` field of the body.

    [FR-04] / NFR-02 / T-05 — security-sensitive statuses (those in
    `_INSTANCE_SCRUB_STATUSES`) get an empty string because the request
    URL path would itself disclose something the caller is not
    authorised to know. When the request has no URL (e.g. a stub
    passed by a coverage test) we also return an empty string.
    """
    if status in _INSTANCE_SCRUB_STATUSES:
        return ""
    url = getattr(request, "url", None)
    if url is None:
        return ""
    return str(url.path)


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
        "type": _DEFAULT_TYPE,
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


def _envelope_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """Build the canonical problem+json response (shared by every path).

    [FR-10] — AC-10.1 / AC-10.2 / AC-10.4 / AC-10.5. Shared by every
    code path that surfaces a structured error — the exception
    handlers (``problem_json_response``) and the direct-emission helper
    (``render_problem``) — so the envelope shape, response headers,
    and log line stay byte-identical across all paths.
    """
    correlation_id = _generate_correlation_id(request)
    instance = _resolve_instance(request, status)
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


def _resolve_exception_envelope(
    exc: Exception,
) -> Tuple[int, str, str, Dict[str, str]]:
    """Pick the canonical (status, title, detail, headers) for an exception.

    [FR-10] — AC-10.3 / AC-10.7. The generic 500 fallback NEVER echoes
    the exception text into the body so stack / SQL / path / traceback /
    query fragments can never surface. ``TaskQError`` carries its own
    headers (e.g. ``Retry-After`` for 429 per FR-05 AC-5.1) which the
    caller round-trips into the response.
    """
    if isinstance(exc, TaskQError):
        return (
            exc.status,
            exc.title,
            exc.detail,
            dict(exc.headers or {}),
        )
    if isinstance(exc, RequestValidationError):
        return 422, "Validation Error", "request validation failed", {}
    if isinstance(exc, StarletteHTTPException):
        status = int(exc.status_code)
        title, fallback_detail = _STATUS_ENVELOPES.get(
            status, ("Error", "error")
        )
        detail = (
            str(exc.detail)
            if exc.detail is not None and str(exc.detail)
            else fallback_detail
        )
        return status, title, detail, {}
    # Generic 500 — DO NOT leak the exception text into the body.
    return 500, "Internal Server Error", "internal server error", {}


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
    status, title, detail, extra_headers = _resolve_exception_envelope(exc)
    return _envelope_response(
        request,
        status=status,
        title=title,
        detail=detail,
        extra_headers=extra_headers,
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
    return _envelope_response(
        request,
        status=status,
        title=title,
        detail=detail,
        extra_headers=extra_headers,
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
