"""v1 — initial schema for FR-07.

[FR-07] alembic revision #1 — establishes the base tables (`tasks`,
`api_keys`) that v2 and v3 evolve.

The v1 downgrade does NOT use any raw destructive-shortcut helper
(the forbidden pattern from AC-7.6); it uses alembic's typed
`op.drop_table` helper which generates a real DDL `DROP TABLE`
statement at runtime.

Citations:
- 02-architecture/TEST_SPEC.md §FR-07 (AC-7.1, AC-7.6)
- 03-development/src/migrations/versions/v2_tags.py — depends on v1
- 03-development/src/migrations/versions/v3_split_results.py — depends on v2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "v1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `tasks` and `api_keys` — the v1 baseline."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    """Drop `tasks` and `api_keys` — reverse of v1's upgrade."""
    # Real downgrade: typed drop_table helper, NOT a raw destructive
    # shortcut bypassing proper alembic flow (AC-7.6).
    op.drop_table("api_keys")
    op.drop_table("tasks")