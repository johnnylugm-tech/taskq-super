"""Runtime configuration loaded from environment.

[FR-01] — env-driven defaults for SQLAlchemy URL, list limits, etc.

Citations:
- taskq_api.config:line 16-30  env override per FR-01 NFR-07 license/dependency discipline
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot.

    Citations:
    - taskq_api.config:Settings  base settings struct consumed by session + app
    """

    database_url: str = "sqlite+pysqlite:///./taskq.db"
    default_list_limit: int = 50
    max_list_limit: int = 200
    min_list_limit: int = 1


def get_settings() -> Settings:
    """Read env vars (TASKQ_DB_URL, TASKQ_DEFAULT_LIMIT, TASKQ_MAX_LIMIT)."""
    return Settings(
        database_url=os.environ.get("TASKQ_DB_URL", Settings.database_url),
        default_list_limit=int(
            os.environ.get("TASKQ_DEFAULT_LIMIT", str(Settings.default_list_limit))
        ),
        max_list_limit=int(
            os.environ.get("TASKQ_MAX_LIMIT", str(Settings.max_list_limit))
        ),
        min_list_limit=int(
            os.environ.get("TASKQ_MIN_LIMIT", str(Settings.min_list_limit))
        ),
    )


__all__: list[str] = ["Settings", "get_settings"]
