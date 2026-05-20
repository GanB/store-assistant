"""Thread management — the operator-facing API over the LangGraph checkpoint store.

The agent itself only reads/writes through the LangGraph checkpointer. This
module provides the cross-table operations that don't fit inside a single
checkpointer call: list past conversations for the UI, fork a conversation
at a known checkpoint, hard-delete every trace of a thread (right-to-deletion).

Each operation owns a connection pool for its lifetime. The pool is bound
to the active asyncio event loop and is closed before the function returns.
This trades a small per-call connection cost for the simplest possible
concurrency model: no shared state across calls or event loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, TypedDict
from urllib.parse import quote

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from store_assistant.agent.checkpointer import (
    CHECKPOINT_SCHEMA,
    CheckpointerNotConfiguredError,
)
from store_assistant.config import Settings
from store_assistant.logging_config import get_logger

_log = get_logger("store_assistant.threads")


class ThreadSummary(TypedDict):
    thread_id: str
    created_at: datetime
    last_updated_at: datetime
    turn_count: int
    current_node: str | None
    is_terminated: bool


class CheckpointSummary(TypedDict):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    created_at: datetime | None
    source: str | None
    step: int | None


class ThreadState(TypedDict):
    thread_id: str
    last_checkpoint_id: str | None
    messages: list[dict[str, str]]
    is_terminated: bool


def _conn_url(settings: Settings) -> str:
    if settings.agent_checkpoint_db_url is None:
        raise CheckpointerNotConfiguredError(
            "AGENT_CHECKPOINT_DB_URL is not set."
        )
    raw = settings.agent_checkpoint_db_url.get_secret_value()
    options = quote(f"-c search_path={CHECKPOINT_SCHEMA},public", safe="")
    sep = "&" if "?" in raw else "?"
    return f"{raw}{sep}options={options}"


@asynccontextmanager
async def _pool(
    settings: Settings,
) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        conninfo=_conn_url(settings),
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True},
        open=False,
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _extract_messages(checkpoint_jsonb: dict[str, Any]) -> list[dict[str, str]]:
    """Pull a renderable message list out of a checkpoint payload.

    LangGraph stores messages under channel_values.messages. Each entry is
    a serialised LangChain message; we only need (role, content) for the
    UI. Anything we don't recognise is silently skipped — best-effort
    rendering, never an error to the caller.
    """
    if not isinstance(checkpoint_jsonb, dict):
        return []
    channel_values = checkpoint_jsonb.get("channel_values") or {}
    if not isinstance(channel_values, dict):
        return []
    messages = channel_values.get("messages") or []
    if not isinstance(messages, list):
        return []
    out: list[dict[str, str]] = []
    for raw in messages:
        # Messages may be serialised as dicts ({"type": "...", "content": "..."})
        # or as objects with attributes — accept both shapes.
        msg_type = (
            raw.get("type")
            if isinstance(raw, dict)
            else getattr(raw, "type", None)
        )
        content = (
            raw.get("content")
            if isinstance(raw, dict)
            else getattr(raw, "content", None)
        )
        if not msg_type or not isinstance(content, str) or not content.strip():
            continue
        if msg_type == "human":
            out.append({"role": "user", "content": content})
        elif msg_type == "ai":
            out.append({"role": "assistant", "content": content})
        # tool / system messages intentionally skipped for UI rendering.
    return out


async def list_threads(
    settings: Settings,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ThreadSummary]:
    """Returns recent threads ordered by last-checkpoint timestamp DESC."""
    sql = """
        SELECT
            thread_id,
            COUNT(*) AS turn_count,
            MAX((metadata->>'step')::int) AS step,
            MAX(checkpoint_id) AS last_checkpoint_id
        FROM checkpoints
        WHERE checkpoint_ns = ''
        GROUP BY thread_id
        ORDER BY MAX(checkpoint_id) DESC
        LIMIT %s OFFSET %s
    """
    out: list[ThreadSummary] = []
    async with _pool(settings) as pool, pool.connection() as conn:
        cur = await conn.cursor(row_factory=dict_row).execute(sql, (limit, offset))
        async for row in cur:
            # Checkpoint IDs are ULIDs containing a sortable timestamp; we
            # surface them directly rather than parsing because the UI just
            # wants ordering and the actual wall-clock timestamps will come
            # from the conversation_summaries row when one exists.
            out.append(
                ThreadSummary(
                    thread_id=row["thread_id"],
                    created_at=datetime.now(tz=UTC),
                    last_updated_at=datetime.now(tz=UTC),
                    turn_count=int(row["turn_count"]),
                    current_node=None,
                    is_terminated=False,
                )
            )
    return out


async def list_checkpoints(
    settings: Settings,
    thread_id: str,
    *,
    limit: int = 50,
) -> list[CheckpointSummary]:
    sql = """
        SELECT checkpoint_id, parent_checkpoint_id, metadata
        FROM checkpoints
        WHERE thread_id = %s AND checkpoint_ns = ''
        ORDER BY checkpoint_id DESC
        LIMIT %s
    """
    out: list[CheckpointSummary] = []
    async with _pool(settings) as pool, pool.connection() as conn:
        cur = await conn.cursor(row_factory=dict_row).execute(
            sql, (thread_id, limit)
        )
        async for row in cur:
            metadata = row["metadata"] or {}
            step_raw = metadata.get("step") if isinstance(metadata, dict) else None
            step: int | None
            try:
                step = int(step_raw) if step_raw is not None else None
            except (TypeError, ValueError):
                step = None
            out.append(
                CheckpointSummary(
                    checkpoint_id=row["checkpoint_id"],
                    parent_checkpoint_id=row["parent_checkpoint_id"],
                    created_at=None,
                    source=metadata.get("source") if isinstance(metadata, dict) else None,
                    step=step,
                )
            )
    return out


async def get_thread_state(
    settings: Settings,
    thread_id: str,
) -> ThreadState:
    """Returns the latest checkpoint's renderable view for a thread."""
    sql = """
        SELECT checkpoint_id, checkpoint
        FROM checkpoints
        WHERE thread_id = %s AND checkpoint_ns = ''
        ORDER BY checkpoint_id DESC
        LIMIT 1
    """
    async with _pool(settings) as pool, pool.connection() as conn:
        cur = await conn.cursor(row_factory=dict_row).execute(sql, (thread_id,))
        row = await cur.fetchone()
    if row is None:
        return ThreadState(
            thread_id=thread_id,
            last_checkpoint_id=None,
            messages=[],
            is_terminated=False,
        )
    payload = row["checkpoint"] or {}
    messages = _extract_messages(payload)
    channel_values = (
        payload.get("channel_values") if isinstance(payload, dict) else None
    ) or {}
    is_terminated = bool(channel_values.get("terminated"))
    return ThreadState(
        thread_id=thread_id,
        last_checkpoint_id=row["checkpoint_id"],
        messages=messages,
        is_terminated=is_terminated,
    )


# Public API — exposed for replay/audit use cases. Intentionally not surfaced
# in the demo UI; see ADR-015 for rationale.
async def fork_thread(
    settings: Settings,
    source_thread_id: str,
    *,
    at_checkpoint_id: str | None = None,
    new_thread_id: str | None = None,
) -> str:
    """Branch a thread at a checkpoint. Returns the new thread_id.

    Copies every checkpoint, write, and blob row up to and including the
    named checkpoint into a new thread_id, in a single transaction. If
    at_checkpoint_id is None, copies the latest.
    """
    from uuid import uuid4  # noqa: PLC0415 — local import keeps the module light

    target = new_thread_id or f"fork-{uuid4()}"
    async with _pool(settings) as pool, pool.connection() as conn:
        await conn.set_autocommit(False)
        try:
            async with conn.transaction():
                if at_checkpoint_id is None:
                    cur = await conn.execute(
                        "SELECT MAX(checkpoint_id) FROM checkpoints "
                        "WHERE thread_id = %s AND checkpoint_ns = ''",
                        (source_thread_id,),
                    )
                    row = await cur.fetchone()
                    if row is None or row[0] is None:
                        raise ValueError(
                            f"source thread {source_thread_id!r} has no checkpoints"
                        )
                    pin = row[0]
                else:
                    pin = at_checkpoint_id

                await conn.execute(
                    """
                    INSERT INTO checkpoints (
                        thread_id, checkpoint_ns, checkpoint_id,
                        parent_checkpoint_id, type, checkpoint, metadata
                    )
                    SELECT %s, checkpoint_ns, checkpoint_id,
                           parent_checkpoint_id, type, checkpoint, metadata
                    FROM checkpoints
                    WHERE thread_id = %s
                      AND checkpoint_ns = ''
                      AND checkpoint_id <= %s
                    """,
                    (target, source_thread_id, pin),
                )
                await conn.execute(
                    """
                    INSERT INTO checkpoint_writes (
                        thread_id, checkpoint_ns, checkpoint_id, task_id,
                        idx, channel, type, blob, task_path
                    )
                    SELECT %s, checkpoint_ns, checkpoint_id, task_id,
                           idx, channel, type, blob, task_path
                    FROM checkpoint_writes
                    WHERE thread_id = %s
                      AND checkpoint_ns = ''
                      AND checkpoint_id <= %s
                    """,
                    (target, source_thread_id, pin),
                )
                await conn.execute(
                    """
                    INSERT INTO checkpoint_blobs (
                        thread_id, checkpoint_ns, channel, version, type, blob
                    )
                    SELECT %s, checkpoint_ns, channel, version, type, blob
                    FROM checkpoint_blobs
                    WHERE thread_id = %s
                      AND checkpoint_ns = ''
                    """,
                    (target, source_thread_id),
                )
        finally:
            await conn.set_autocommit(True)

    _log.info(
        "threads.fork",
        source_thread_id=source_thread_id,
        target_thread_id=target,
        pin_checkpoint_id=pin,
    )
    return target


async def delete_thread(
    settings: Settings,
    thread_id: str,
) -> None:
    """Hard-delete every trace of a thread.

    Removes checkpoints, writes, blobs (agent_state schema) AND the
    domain rows that share thread_id (stores, conversation_summaries),
    in one transaction. Right-to-deletion mechanic; see ADR-015.
    """
    async with _pool(settings) as pool, pool.connection() as conn:
        await conn.set_autocommit(False)
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM checkpoint_writes "
                    "WHERE thread_id = %s AND checkpoint_ns = ''",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM checkpoint_blobs "
                    "WHERE thread_id = %s AND checkpoint_ns = ''",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM checkpoints "
                    "WHERE thread_id = %s AND checkpoint_ns = ''",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM public.stores WHERE thread_id = %s",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM public.conversation_summaries "
                    "WHERE thread_id = %s",
                    (thread_id,),
                )
                # Per ADR-015 right-to-deletion: scrub the per-turn
                # observability rows alongside the conversation state.
                await conn.execute(
                    "DELETE FROM public.turn_metrics WHERE thread_id = %s",
                    (thread_id,),
                )
        finally:
            await conn.set_autocommit(True)
    _log.info("threads.delete", thread_id=thread_id)
