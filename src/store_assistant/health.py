"""Health checks driving the UI Status panel.

Each check returns a small typed result so the renderer can colour the
row red when something's off and surface a short reason. Checks are
intentionally shallow and side-effect-free — they should never run a
migration or set up a schema, and they should never block the UI for
more than a couple of seconds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from store_assistant.agent.checkpointer import CHECKPOINT_SCHEMA
from store_assistant.config import Settings
from store_assistant.data.migration_check import _expected_head


@dataclass
class HealthResult:
    ok: bool
    label: str
    detail: str | None = None


async def check_db_connected(settings: Settings) -> HealthResult:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        return HealthResult(
            ok=False, label="DB connected", detail=f"unreachable ({type(exc).__name__})"
        )
    finally:
        await engine.dispose()
    return HealthResult(ok=True, label="DB connected")


async def check_migrations_at_head(settings: Settings) -> HealthResult:
    expected = _expected_head()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            current = await conn.run_sync(
                lambda sync_conn: __import__(
                    "alembic.runtime.migration", fromlist=["MigrationContext"]
                ).MigrationContext.configure(sync_conn).get_current_revision()
            )
    except Exception as exc:
        return HealthResult(
            ok=False,
            label="Migrations at head",
            detail=f"check failed ({type(exc).__name__})",
        )
    finally:
        await engine.dispose()

    if current == expected:
        return HealthResult(ok=True, label="Migrations at head")
    return HealthResult(
        ok=False,
        label="Migrations at head",
        detail=f"DRIFT ({current}/{expected})",
    )


async def check_checkpoints_enabled(settings: Settings) -> HealthResult:
    if settings.agent_checkpoint_db_url is None:
        return HealthResult(
            ok=False,
            label="Checkpoints enabled",
            detail="AGENT_CHECKPOINT_DB_URL unset",
        )
    raw = settings.agent_checkpoint_db_url.get_secret_value()
    # AsyncPostgresSaver uses psycopg-style URLs which sqlalchemy's async
    # engine can't drive directly — translate to asyncpg for this probe.
    sa_url = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sa_url)
    try:
        async with engine.connect() as conn:
            # CHECKPOINT_SCHEMA is a module constant ("agent_state"); no
            # user-controlled input flows into the SQL.
            await conn.execute(
                text(f"SELECT 1 FROM {CHECKPOINT_SCHEMA}.checkpoints LIMIT 1")  # noqa: S608  # nosec B608
            )
    except Exception as exc:
        return HealthResult(
            ok=False,
            label="Checkpoints enabled",
            detail=f"probe failed ({type(exc).__name__})",
        )
    finally:
        await engine.dispose()
    return HealthResult(ok=True, label="Checkpoints enabled")


def check_tracing_enabled() -> HealthResult:
    """Env-var-only check; no network call, no caller assumes connectivity."""
    has_key = bool(os.environ.get("LANGSMITH_API_KEY"))
    has_project = bool(os.environ.get("LANGSMITH_PROJECT"))
    if has_key and has_project:
        return HealthResult(ok=True, label="Tracing enabled")
    missing = []
    if not has_key:
        missing.append("LANGSMITH_API_KEY")
    if not has_project:
        missing.append("LANGSMITH_PROJECT")
    return HealthResult(
        ok=False,
        label="Tracing enabled",
        detail="missing " + ", ".join(missing),
    )
