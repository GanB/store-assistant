"""align stores and conversation_summaries schema with models

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-05 00:00:00.000000

The initial migration (0001) was authored with two carry-overs from a
SQLAlchemy autogenerate run that did not reflect the application's
intended behaviour:

1. `stores.id` and `conversation_summaries.id` were given a PostgreSQL
   server_default of `uuid_generate_v4()`. The ORM models, however,
   generate UUIDs in Python via `default=uuid.uuid4` (see
   `src/store_assistant/data/models.py`). Two sources of truth for the
   same value is the kind of drift that bites later — for example,
   when a non-Postgres backend is used in tests (SQLite) and the DB-side
   default silently doesn't fire, or when a future migration needs to
   change UUID generation strategy and has to reconcile both sides.

2. `stores.name` was declared with both `unique=True` and `index=True`
   in the model. SQLAlchemy expresses that as a single unique index
   (`ix_stores_name`, unique). The autogenerate run instead emitted a
   separate UniqueConstraint (`uq_stores_name`) plus a non-unique index
   — functionally equivalent on Postgres, but it diverges from what the
   model declares and so `alembic check` flags it on every CI run.

The model is the source of truth for both. This migration brings the
deployed schema in line with the model.

Note: this is a metadata-only change. It does not rewrite data, does not
hold long locks (the index swap is the only DDL of consequence and runs
in well under a second on any realistic stores-table size), and it
preserves the `name` uniqueness invariant across the cutover by always
having at least one unique enforcement in place during the swap.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop redundant DB-side UUID defaults; the ORM owns ID generation.
    op.alter_column("stores", "id", server_default=None)
    op.alter_column("conversation_summaries", "id", server_default=None)

    # Replace (UniqueConstraint + non-unique index) with a single unique index,
    # mirroring `String(255), unique=True, index=True` on the model.
    # The unique index goes in BEFORE the constraint goes out so the column
    # is never momentarily unprotected.
    op.drop_index(op.f("ix_stores_name"), table_name="stores")
    op.create_index(
        op.f("ix_stores_name"), "stores", ["name"], unique=True
    )
    op.drop_constraint(op.f("uq_stores_name"), "stores", type_="unique")


def downgrade() -> None:
    # Restore the original (constraint + non-unique index) shape.
    op.create_unique_constraint(op.f("uq_stores_name"), "stores", ["name"])
    op.drop_index(op.f("ix_stores_name"), table_name="stores")
    op.create_index(
        op.f("ix_stores_name"), "stores", ["name"], unique=False
    )

    # Restore the DB-side UUID defaults that 0001 originally created.
    op.alter_column(
        "conversation_summaries",
        "id",
        server_default=sa.text("uuid_generate_v4()"),
    )
    op.alter_column(
        "stores",
        "id",
        server_default=sa.text("uuid_generate_v4()"),
    )
