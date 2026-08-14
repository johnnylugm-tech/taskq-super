"""problem+json error helpers — RFC 7807 shape.

[FR-01] — TaskQError + problem() for FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7.
[FR-05] — AC-5.1 / AC-5.2: problem() carries optional `headers` so the
rate-limit gate can attach `Retry-After: <seconds>` to a 429 response
without bypassing the problem+json handler.

Citations:
- taskq_api.errors:line 18-44  TaskQError & problem() per FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7
- taskq_api.errors:problem  extended with `headers=` per FR-05 AC-5.1
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


async def problem_json_response(
    request: Request,  # noqa: ARG001
    exc: Exception,
) -> JSONResponse:
    """Render an exception as application/problem+json (RFC 7807)."""
    if isinstance(exc, TaskQError):
        body = {
            "type": "about:blank",
            "title": exc.title,
            "status": exc.status,
            "detail": exc.detail,
        }
        return JSONResponse(
            status_code=exc.status,
            content=body,
            media_type="application/problem+json",
            headers=exc.headers or None,
        )

    if isinstance(exc, RequestValidationError):
        body = {
            "type": "about:blank",
            "title": "Validation Error",
            "status": 422,
            "detail": str(exc.errors()),
        }
        return JSONResponse(
            status_code=422,
            content=body,
            media_type="application/problem+json",
        )

    # Fallback: 500 with a generic problem+json.
    body = {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": str(exc) if exc else "",
    }
    return JSONResponse(
        status_code=500,
        content=body,
        media_type="application/problem+json",
    )


def install_exception_handlers(app) -> None:  # noqa: ANN001
    """Register problem+json handlers on a FastAPI app.

    Citations:
    - taskq_api.errors:install_exception_handlers  wires AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7
    """
    app.add_exception_handler(TaskQError, problem_json_response)
    app.add_exception_handler(RequestValidationError, problem_json_response)
    app.add_exception_handler(Exception, problem_json_response)


__all__: list[str] = ["TaskQError", "problem", "install_exception_handlers", "problem_json_response"]
