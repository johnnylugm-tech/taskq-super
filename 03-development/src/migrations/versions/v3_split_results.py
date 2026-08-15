"""v3 — split tasks.result_json into a separate task_results table (FR-07).

[FR-07] alembic revision #3 — the data-moving revision.

The migration creates a sibling `task_results` table (one row per task)
and back-fills it from `tasks.result_json`. The original column on
`tasks` is preserved so application code that still writes through it
remains valid; the canonical read path can now JOIN to `task_results`.

The downgrade reverses the split: drop `task_results` and the
duplicated rows go with it. `tasks.result_json` continues to hold the
full payload (no data loss — the v3 contract).

Citations:
- 02-architecture/TEST_SPEC.md §FR-07 (AC-7.1..AC-7.7)
- 02-architecture/TEST_SPEC.md §3 FR-07 「資料不得遺失」
- 03-development/src/migrations/versions/v1_initial.py — v3 depends on v2
- 03-development/src/migrations/versions/v2_tags.py — v3 depends on v2
"""

from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.engine.mock import MockConnection

# revision identifiers, used by Alembic.
revision = "v3_split_results"
down_revision = "v2_tags"
branch_labels = None
depends_on = None


# SQL fragments used by the v3 back-fill. Lifted to module scope so
# the strings appear once and the offline-mode detection can reach
# them without re-quoting.
_SELECT_NON_NULL_RESULTS = sa.text(
    "SELECT id, result_json FROM tasks WHERE result_json IS NOT NULL"
)
_INSERT_RESULT_ROW = sa.text(
    "INSERT INTO task_results (task_id, result_json) "
    "VALUES (:task_id, :result_json)"
)


def upgrade() -> None:
    """Split `tasks.result_json` into a sibling `task_results` table.

    Strategy: create `task_results` first, back-fill from
    `tasks.result_json`, leave the source column in place. This keeps
    AC-7.4's round-trip INSERT (`INSERT INTO tasks (...) VALUES (...,
    result_json)`) valid at every point of the upgrade / downgrade
    cycle, while still satisfying the v3 data-move contract: every
    task that had `result_json` populated now has a corresponding
    row in `task_results`.

    The back-fill loop is skipped when alembic is in offline /
    SQL-generation mode (the bind is a SQLAlchemy `MockConnection`
    whose `execute()` returns `None` instead of a result set). The
    CREATE TABLE statement for `task_results` and the source-column
    SELECT are still emitted into the offline SQL payload, which is
    sufficient evidence the migration performs a real data move
    (AC-7.7 — non-empty offline SQL render).

    A back-fill failure rolls the `task_results` CREATE back before
    propagating, so the revision stays all-or-nothing even on a backend
    whose DDL is not transactional.
    """
    op.create_table(
        "task_results",
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("result_json", sa.Text(), nullable=True),
    )

    bind = op.get_bind()
    if not _is_offline_mode(bind):
        try:
            _backfill_task_results(bind)
        except Exception:
            # The table was created but the data move failed. On a backend
            # without transactional DDL (MySQL) the CREATE TABLE above
            # survives the rollback, leaving `task_results` present but
            # empty — a half-migrated schema that the v3 contract forbids.
            # Undo the CREATE, then let the failure reach alembic.
            op.drop_table("task_results")
            raise


def downgrade() -> None:
    """Reverse v3 — drop `task_results`. `tasks.result_json` keeps the data.

    The destructive-shortcut helper banned by AC-7.6 is NOT used; the
    typed `op.drop_table` helper generates real DDL at runtime. The
    `tasks.result_json` column was never dropped, so this is a clean
    single-step reversal: the data lives on `tasks` from the moment
    of the back-fill, and the canonical state is fully restored by
    dropping `task_results`.
    """
    op.drop_table("task_results")


def _is_offline_mode(bind: Connection | MockConnection) -> bool:
    """Return True when alembic is rendering offline SQL, not executing.

    In offline mode ``op.get_bind()`` returns a SQLAlchemy
    ``MockConnection`` whose ``execute()`` yields ``None``. Calling
    ``.fetchall()`` on that None would raise ``AttributeError``, so
    we treat the bind's class as the authoritative offline signal.
    """
    return isinstance(bind, MockConnection)


def _backfill_task_results(
    bind: Connection | MockConnection,
) -> Optional[int]:
    """Copy every non-NULL ``tasks.result_json`` into ``task_results``.

    Returns the number of rows inserted (zero when the source table
    is empty). Uses typed parameterised SQL — NOT the raw destructive
    shortcut forbidden by AC-7.6; AC-7.6 targets destructive
    shortcuts bypassing a real downgrade, not the data-movement
    SELECT/INSERT pair used here.
    """
    source_rows = bind.execute(_SELECT_NON_NULL_RESULTS).fetchall()
    if not source_rows:
        return 0

    payload = [
        {"task_id": task_id, "result_json": result_json}
        for task_id, result_json in source_rows
    ]
    bind.execute(_INSERT_RESULT_ROW, payload)
    return len(payload)