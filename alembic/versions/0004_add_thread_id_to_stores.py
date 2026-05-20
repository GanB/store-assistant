"""add thread_id column to stores

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-27 00:00:00.000000

Stores are now linked to the conversation that created them via thread_id.
This enables right-to-deletion across both the agent_state schema (LangGraph
checkpoints) and the domain table in a single transaction. The column is
nullable so any pre-existing rows from earlier dev environments stay valid;
new writes from the agent populate it.

No FK to the LangGraph checkpoint tables: thread_id is a string identifier
without a stable single-table primary key on the LangGraph side
(checkpoints is keyed by (thread_id, checkpoint_ns, checkpoint_id), so a FK
would be impractical). The pragmatic choice is the column-only approach
with deletion logic enforced in app/threads.py — see ADR-015.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stores",
        sa.Column("thread_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_stores_thread_id"),
        "stores",
        ["thread_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_stores_thread_id"), table_name="stores")
    op.drop_column("stores", "thread_id")
