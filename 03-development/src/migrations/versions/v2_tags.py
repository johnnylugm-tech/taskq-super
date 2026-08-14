"""v2 — add tags + unique index on tasks.name (FR-07).

[FR-07] alembic revision #2 — evolves the v1 schema by adding:
  - `tags` table (tag definitions)
  - `task_tags` association table (many-to-many between tasks and tags)
  - Unique index on `tasks.name` (idempotency for task creation)

The v2 downgrade reverses each of those additions while leaving the v1
data in `tasks` and `api_keys` intact (per FR-07 v2 row of the table).

Citations:
- 02-architecture/TEST_SPEC.md §FR-07 (AC-7.1, AC-7.2, AC-7.6)
- 03-development/src/migrations/versions/v1_initial.py — v2 depends on v1
- 03-development/src/migrations/versions/v3_split_results.py — v3 depends on v2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_tags"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


# Single source of truth for the new objects added by v2. The downgrade
# iterates this list in reverse so the destruction order mirrors the
# upgrade construction order.
_V2_NEW_OBJECTS: tuple[str, ...] = ("ix_tasks_name_unique", "task_tags", "tags")
_V2_INDEX_NAME = _V2_NEW_OBJECTS[0]


def upgrade() -> None:
    """Add `tags`, `task_tags`, and the unique index on `tasks.name`."""
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
    )

    op.create_table(
        "task_tags",
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Unique index on `tasks.name` for idempotent task creation.
    op.create_index(_V2_INDEX_NAME, "tasks", ["name"], unique=True)


def downgrade() -> None:
    """Drop the v2 additions; v1 data in `tasks` and `api_keys` stays.

    No raw destructive-shortcut helper is used here — the typed
    `op.drop_index` / `op.drop_table` helpers generate real DDL at
    runtime (AC-7.6). The drop order mirrors the v2 upgrade's create
    order in reverse, so each foreign key target outlives its
    referencing object.
    """
    op.drop_index(_V2_INDEX_NAME, table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")