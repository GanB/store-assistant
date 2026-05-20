"""add turn_metrics table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-28 00:00:00.000000

Per-turn observability rows. Written by the agent layer on every LLM
invocation; queried by the UI for the per-turn metadata strip and the
session-totals panel. Single composite index on (thread_id, turn_index)
covers the per-thread queries the UI runs; a second single-column index
on thread_id keeps cleanup deletions cheap (the right-to-deletion path
in app/threads.py joins on thread_id).

JSONB tool_calls payload mirrors store_assistant.metrics.ToolCallSummary —
arg KEYS only, never values, by invariant (T607).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=True),
        sa.Column(
            "input_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "output_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "cache_read_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "cache_creation_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=6),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "latency_ms", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("langsmith_run_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turn_metrics")),
    )
    op.create_index(
        op.f("ix_turn_metrics_thread_id"),
        "turn_metrics",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_turn_metrics_thread_turn",
        "turn_metrics",
        ["thread_id", "turn_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_turn_metrics_thread_turn", table_name="turn_metrics")
    op.drop_index(op.f("ix_turn_metrics_thread_id"), table_name="turn_metrics")
    op.drop_table("turn_metrics")
