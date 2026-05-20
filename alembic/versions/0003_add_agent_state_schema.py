"""add agent_state schema for langgraph checkpointer

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27 00:00:00.000000

Creates the dedicated agent_state schema with the four tables that
LangGraph's PostgresSaver expects: checkpoints, checkpoint_writes,
checkpoint_blobs, and the checkpoint_migrations housekeeping table.

The DDL was discovered by running PostgresSaver.setup() against a throwaway
schema and inspecting information_schema. The saver looks for
checkpoint_migrations.v to decide whether to run its own setup; we seed it
to the current high-water mark so the saver treats the schema as already
provisioned and never executes DDL at runtime. That keeps the migration-
only invariant from ADR-009 intact.

Note: the LangGraph checkpointer schema is owned by LangGraph. If the
upstream package adds a new internal migration, we must add an Alembic
migration that mirrors the new DDL and bumps the seeded `v`. ADR-015
documents the upgrade procedure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent_state"

# Number of internal migrations LangGraph's PostgresSaver currently knows
# about. Bump only when a new Alembic migration in this repo applies the
# corresponding DDL change. langgraph-checkpoint-postgres ships
# BasePostgresSaver.MIGRATIONS with this many entries on the version we
# are pinned against; the saver treats max(v) as the current schema version.
LANGGRAPH_INTERNAL_MIGRATION_COUNT = 10


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("v", name=op.f("pk_checkpoint_migrations")),
        schema=SCHEMA,
    )

    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''::text"),
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            name=op.f("pk_checkpoints"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "checkpoints_thread_id_idx",
        "checkpoints",
        ["thread_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''::text"),
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column(
            "task_path",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''::text"),
        ),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            name=op.f("pk_checkpoint_writes"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "checkpoint_writes_thread_id_idx",
        "checkpoint_writes",
        ["thread_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''::text"),
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            name=op.f("pk_checkpoint_blobs"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_blobs",
        ["thread_id"],
        schema=SCHEMA,
    )

    # Seed checkpoint_migrations with the highest internal version so
    # PostgresSaver.setup() treats the schema as already set up. Without
    # this, the first call to a checkpointer method would attempt to apply
    # the saver's own DDL — re-introducing the runtime-DDL path we are
    # explicitly avoiding (ADR-009 / ADR-015).
    for v in range(LANGGRAPH_INTERNAL_MIGRATION_COUNT):
        op.execute(
            sa.text(
                f'INSERT INTO "{SCHEMA}".checkpoint_migrations (v) VALUES (:v)'
            ).bindparams(v=v)
        )


def downgrade() -> None:
    op.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
