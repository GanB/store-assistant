"""add thread_id to conversation_summaries

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-27 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_summaries",
        sa.Column("thread_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_conversation_summaries_thread_id"),
        "conversation_summaries",
        ["thread_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversation_summaries_thread_id"),
        table_name="conversation_summaries",
    )
    op.drop_column("conversation_summaries", "thread_id")
