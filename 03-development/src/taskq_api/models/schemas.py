"""Pydantic request/response schemas for FR-01.

[FR-01] — `TaskCreate` body validator. Empty / oversize / blacklisted input
must yield HTTP 422 (AC-1.3).

Citations:
- taskq_api.models.schemas:TaskCreate  body for POST /v1/tasks per SPEC §3 FR-01
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# Default bounds per SPEC §3 FR-01 (length 1..1000 chars).
_NAME_MIN = 1
_NAME_MAX = 1000
_COMMAND_MIN = 1
_COMMAND_MAX = 1000

# Conservative injection blacklist (shell metachars + control).
_INJECTION_PATTERNS = (
    r"[;&|`$<>\\]\r",
    r"\$\(",
    r"`",
)


def _no_injection(value: str) -> str:
    """Reject strings that look like shell injection attempts.

    Citations:
    - taskq_api.models.schemas:_no_injection  per SPEC §3 FR-01 black-list
    """
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, value):
            raise ValueError("contains disallowed characters")
    return value


class TaskCreate(BaseModel):
    """Body for POST /v1/tasks.

    Citations:
    - taskq_api.models.schemas:TaskCreate  per FR-01 + AC-1.3
    """

    name: str = Field(..., min_length=_NAME_MIN, max_length=_NAME_MAX)
    command: str = Field(..., min_length=_COMMAND_MIN, max_length=_COMMAND_MAX)
    status: Optional[str] = Field(default="pending")

    @field_validator("name")
    @classmethod
    def _name_safe(cls, v: str) -> str:
        # [FR-01]
        return _no_injection(v)

    @field_validator("command")
    @classmethod
    def _command_safe(cls, v: str) -> str:
        # [FR-01]
        return _no_injection(v)


class TaskOut(BaseModel):
    """Response shape for GET /v1/tasks/{id} and list responses."""

    id: str
    name: str
    command: str
    status: str
    created_at: str


__all__: list[str] = ["TaskCreate", "TaskOut"]
