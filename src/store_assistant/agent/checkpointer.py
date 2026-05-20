"""LangGraph checkpoint wiring.

The production checkpointer is `langgraph.checkpoint.postgres.AsyncPostgresSaver`
backed by the `agent_state` schema (created by alembic migration 0003). It is
constructed once per process and passed to `build_graph(checkpointer=...)`.

Two non-obvious invariants:

1. We never call `.setup()` on the saver. The `agent_state` schema and its
   `checkpoint_migrations` row are owned by alembic; the saver treats the
   schema as already provisioned. This keeps the migration-only invariant
   from ADR-009 intact (and is enforced by T501).

2. The saver shares a connection pool with the rest of the app via psycopg3.
   We hold the pool open for the process lifetime; the caller is responsible
   for awaiting `aclose()` on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.errors import UndefinedTable
from psycopg_pool import AsyncConnectionPool

from store_assistant.config import Settings

CHECKPOINT_SCHEMA = "agent_state"


class CheckpointerNotConfiguredError(RuntimeError):
    """AGENT_CHECKPOINT_DB_URL was not set or the schema is not provisioned."""


def _conn_url(settings: Settings) -> str:
    if settings.agent_checkpoint_db_url is None:
        raise CheckpointerNotConfiguredError(
            "AGENT_CHECKPOINT_DB_URL is not set. Set it to a Postgres "
            "connection string (psycopg-style) and run `make db-migrate` "
            "to provision the agent_state schema before starting the app."
        )
    raw = settings.agent_checkpoint_db_url.get_secret_value()
    # Pin the search path so the saver's unqualified table references land
    # in agent_state. The value of `options=` is libpq-quoted (spaces, =,
    # commas all need URL-encoding inside the URI).
    options = quote(f"-c search_path={CHECKPOINT_SCHEMA},public", safe="")
    sep = "&" if "?" in raw else "?"
    return f"{raw}{sep}options={options}"


async def _verify_schema_present(pool: AsyncConnectionPool[AsyncConnection]) -> None:
    # The probe is intentionally unqualified — it relies on the search_path
    # set in _conn_url so a misconfigured connection (search_path missing
    # agent_state) is caught here rather than producing confusing errors
    # later when the saver tries to write.
    async with pool.connection() as conn:
        try:
            cur = await conn.execute("SELECT 1 FROM checkpoints LIMIT 1")
            await cur.fetchall()
        except UndefinedTable as exc:
            raise CheckpointerNotConfiguredError(
                f"Schema {CHECKPOINT_SCHEMA!r} is not visible on the "
                "configured connection. Run `make db-migrate` "
                "(or `alembic upgrade head`) and verify "
                "AGENT_CHECKPOINT_DB_URL points at the right database."
            ) from exc


@asynccontextmanager
async def production_checkpointer(
    settings: Settings,
) -> AsyncIterator[tuple[AsyncPostgresSaver, AsyncConnectionPool[AsyncConnection]]]:
    """Yield a (saver, pool) pair scoped to the caller's lifetime.

    Use as `async with production_checkpointer(settings) as (saver, pool): ...`.
    The pool is opened on entry and closed on exit. The saver does not own
    the pool's lifecycle; callers can use the pool for other queries
    (the deletion path needs this).
    """
    url = _conn_url(settings)
    # `kwargs={"autocommit": True}` is what AsyncPostgresSaver expects so
    # each checkpoint write commits immediately. Without it we'd get the
    # default "behaviour-undefined-when-implicit-tx" warning from psycopg.
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        conninfo=url,
        max_size=10,
        kwargs={"autocommit": True},
        open=False,
    )
    await pool.open()
    try:
        await _verify_schema_present(pool)
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        yield saver, pool
    finally:
        await pool.close()
