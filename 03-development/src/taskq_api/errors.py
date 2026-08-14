"""problem+json error helpers — RFC 7807 shape.

Citations:
- taskq_api.errors:line 18-44  TaskQError & problem() per FR-01 AC-1.2/AC-1.3/AC-1.5/AC-1.6/AC-1.7
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class TaskQError(Exception):
    """Raised by service/handler code to surface a structured problem+json."""

    def __init__(self, status: int, title: str, detail: str = "") -> None:
        # [FR-01]
        self.status = status
        self.title = title
        self.detail = detail


def problem(status: int, title: str, detail: str = "") -> TaskQError:
    """Build a TaskQError that the global handler renders as problem+json."""
    return TaskQError(status=status, title=title, detail=detail)


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
