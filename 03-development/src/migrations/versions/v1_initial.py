"""v1 — initial schema for FR-07.

[FR-07] alembic revision #1 — establishes the base tables (`tasks`,
`api_keys`) that v2 and v3 evolve.

The v1 downgrade uses alembic's typed `op.drop_table` helper which
generates a real DDL ``DROP TABLE`` statement at runtime; it does NOT
fall back to a raw destructive shortcut (AC-7.6).

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


# Column type shorthands used by both v1 tables. Centralising them keeps
# the v1 column declarations on a single screen and makes any future
# tightening (length, nullability, server defaults) a one-line change.
_TASK_ID_LEN = 64
_TASK_NAME_LEN = 255
_TASK_COMMAND_LEN = 1024
_TASK_STATUS_LEN = 32
_API_KEY_HASH_LEN = 128
_API_KEY_SCOPE_LEN = 32


def upgrade() -> None:
    """Create the FR-07 baseline tables (`tasks`, `api_keys`)."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=_TASK_ID_LEN), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=_TASK_NAME_LEN), nullable=False),
        sa.Column("command", sa.String(length=_TASK_COMMAND_LEN), nullable=False),
        sa.Column(
            "status",
            sa.String(length=_TASK_STATUS_LEN),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "key_hash",
            sa.String(length=_API_KEY_HASH_LEN),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "scope",
            sa.String(length=_API_KEY_SCOPE_LEN),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    """Drop `tasks` and `api_keys` — the v1 upgrade reversed."""
    # Real downgrade: typed drop_table helper, NOT a raw destructive
    # shortcut bypassing proper alembic flow (AC-7.6).
    op.drop_table("api_keys")
    op.drop_table("tasks")