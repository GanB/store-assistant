"""Real-Postgres integration tests for the checkpointer + threads (T503-T508,
T510, T512).

Each test runs against the live Postgres referenced by AGENT_CHECKPOINT_DB_URL.
Tests that don't have that env var set are skipped (the unit tier covers the
static invariants regardless). Each test isolates by using a unique thread_id
so they can run in parallel against the same database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.agent.checkpointer import production_checkpointer
from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings
from store_assistant.threads import (
    delete_thread,
    fork_thread,
    list_checkpoints,
)
from tests.conftest import MockChatModel

REPO_ROOT = Path(__file__).resolve().parents[2]


def _live_settings() -> Settings:
    return Settings(
        anthropic_api_key=SecretStr("test"),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr("test-pass"),
        agent_checkpoint_db_url=SecretStr(os.environ["AGENT_CHECKPOINT_DB_URL"]),
    )


pytestmark = pytest.mark.skipif(
    "AGENT_CHECKPOINT_DB_URL" not in os.environ,
    reason="checkpointing tests require a reachable Postgres + AGENT_CHECKPOINT_DB_URL",
)


def _new_thread_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


async def _seed_checkpoints(settings: Settings, thread_id: str, n: int) -> list[str]:
    """Write `n` synthetic checkpoints under thread_id; return their ids in
    insertion order. Uses fixed prefix + zero-padded counter so the ids
    sort lexicographically the way the saver's queries expect.
    """
    ids: list[str] = []
    base_id = uuid4().hex
    async with production_checkpointer(settings) as (saver, _pool):
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
        }
        for i in range(n):
            cp = empty_checkpoint()
            cp["id"] = f"{base_id}-{i:04d}"
            metadata: dict[str, Any] = {
                "step": i,
                "source": "input" if i == 0 else "loop",
            }
            await saver.aput(config, cp, metadata, {})  # type: ignore[arg-type]
            ids.append(cp["id"])
    return ids


# ---------- T503 ----------
@pytest.mark.asyncio
async def test_t503_fork_copies_up_to_named_checkpoint() -> None:
    """fork_thread copies every checkpoint UP TO the named checkpoint_id and
    nothing after, in a single transaction.
    """
    settings = _live_settings()
    source = _new_thread_id("t503-src")
    target = _new_thread_id("t503-fork")
    try:
        ids = await _seed_checkpoints(settings, source, n=4)
        # Fork at the second checkpoint — first two should land in target,
        # last two should not.
        pin = ids[1]
        new_id = await fork_thread(
            settings, source, at_checkpoint_id=pin, new_thread_id=target
        )
        assert new_id == target

        target_cps = await list_checkpoints(settings, target)
        target_ids = {c["checkpoint_id"] for c in target_cps}
        # Forked thread has only checkpoints <= pin
        assert ids[0] in target_ids
        assert ids[1] in target_ids
        assert ids[2] not in target_ids
        assert ids[3] not in target_ids
    finally:
        await delete_thread(settings, source)
        await delete_thread(settings, target)


# ---------- T504 ----------
@pytest.mark.asyncio
async def test_t504_kill_and_resume_preserves_state(
    async_session: AsyncSession, mock_llm: MockChatModel
) -> None:
    """Headline integration: run a turn, tear down the checkpointer (mimics a
    process restart), then verify a fresh checkpointer sees the prior state.
    """
    from store_assistant.data.repository import (  # noqa: PLC0415
        StoreRepository,
        SummaryRepository,
    )
    from tests.conftest import make_save_call, make_text_response  # noqa: PLC0415

    settings = _live_settings()
    thread_id = _new_thread_id("t504")
    config = {"configurable": {"thread_id": thread_id}}

    try:
        # First lifetime: build graph, save a store, store_id and message
        # state get checkpointed to Postgres.
        store_repo = StoreRepository(async_session)
        summary_repo = SummaryRepository(async_session)
        mock_llm.queue(
            make_save_call("Resilience Mart", "919-555-0001"),
            make_text_response("Saved Resilience Mart."),
        )
        async with production_checkpointer(settings) as (saver1, _pool1):
            graph = build_graph(
                settings, store_repo, summary_repo, llm=mock_llm, checkpointer=saver1
            )
            result1 = await graph.ainvoke(
                {"messages": [HumanMessage("Save Resilience Mart, 919-555-0001")]},
                config=config,
            )
            assert result1.get("terminated") is not True
            n_messages_before = len(result1["messages"])
            assert n_messages_before >= 2

        # Second lifetime: brand-new saver, brand-new graph. Same thread_id.
        # The state must be readable.
        async with production_checkpointer(settings) as (saver2, _pool2):
            graph2 = build_graph(
                settings, store_repo, summary_repo, llm=mock_llm, checkpointer=saver2
            )
            snap = await graph2.aget_state(config)
            recovered_messages = snap.values.get("messages", [])
            assert len(recovered_messages) == n_messages_before, (
                "messages count after restart must match pre-restart"
            )
            # The save tool was called and the row exists in the conftest's
            # SQLite db (the repo uses a session that survives the test).
            saved = await store_repo.get_store_by_name("Resilience Mart")
            assert saved is not None
            assert saved.phone_e164 == "+19195550001"
    finally:
        await delete_thread(settings, thread_id)


# ---------- T505 ----------
@pytest.mark.asyncio
async def test_t505_fork_produces_independent_branch(
    async_session: AsyncSession, mock_llm: MockChatModel
) -> None:
    """Branch a conversation, take divergent action in the new thread, verify
    the original thread is unchanged."""
    from store_assistant.data.repository import (  # noqa: PLC0415
        StoreRepository,
        SummaryRepository,
    )
    from tests.conftest import make_save_call, make_text_response  # noqa: PLC0415

    settings = _live_settings()
    source_thread = _new_thread_id("t505-src")
    fork_target = _new_thread_id("t505-fork")
    try:
        store_repo = StoreRepository(async_session)
        summary_repo = SummaryRepository(async_session)
        mock_llm.queue(
            make_save_call("Branch A Store", "212-555-0010"),
            make_text_response("Saved Branch A Store."),
            make_save_call("Branch A Store", "415-555-9999"),
            make_text_response("Already exists in source."),
            make_save_call("Branch B Store", "415-555-0020"),
            make_text_response("Saved Branch B Store on the fork."),
        )

        async with production_checkpointer(settings) as (saver, _pool):
            graph = build_graph(
                settings, store_repo, summary_repo, llm=mock_llm, checkpointer=saver
            )
            await graph.ainvoke(
                {"messages": [HumanMessage("Save Branch A Store, 212-555-0010")]},
                config={"configurable": {"thread_id": source_thread}},
            )

        # Fork at latest. Use the new thread_id for divergent action.
        forked = await fork_thread(
            settings, source_thread, new_thread_id=fork_target
        )
        assert forked == fork_target

        async with production_checkpointer(settings) as (saver2, _pool2):
            graph2 = build_graph(
                settings, store_repo, summary_repo, llm=mock_llm, checkpointer=saver2
            )
            await graph2.ainvoke(
                {"messages": [HumanMessage("Save Branch B Store, 415-555-0020")]},
                config={"configurable": {"thread_id": fork_target}},
            )
            # Source thread should not have Branch B in its message history.
            src_state = await graph2.aget_state(
                {"configurable": {"thread_id": source_thread}}
            )
            src_text = " ".join(
                str(getattr(m, "content", "")) for m in src_state.values["messages"]
            )
            assert "Branch B Store" not in src_text
    finally:
        await delete_thread(settings, source_thread)
        await delete_thread(settings, fork_target)


# ---------- T506 ----------
@pytest.mark.asyncio
async def test_t506_delete_removes_checkpoints_and_domain_rows() -> None:
    """delete_thread cleans both agent_state and the domain tables in one
    transaction.
    """
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415

    from store_assistant.data.models import (  # noqa: PLC0415
        ConversationSummary,
        Store,
    )

    settings = _live_settings()
    thread_id = _new_thread_id("t506")
    try:
        # Seed a checkpoint
        await _seed_checkpoints(settings, thread_id, n=2)

        # Seed a store and a summary tied to this thread_id (against the
        # real Postgres, so domain-table cleanup is observable). Reuse the
        # checkpoint URL since the same Postgres instance hosts both the
        # agent_state schema and the domain tables.
        domain_url = os.environ["AGENT_CHECKPOINT_DB_URL"].replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        engine = create_async_engine(domain_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            s.add(
                Store(
                    name=f"t506-store-{thread_id}",
                    phone_e164="+12015550001",
                    thread_id=thread_id,
                )
            )
            s.add(
                ConversationSummary(
                    summary_text="t506 summary",
                    thread_id=thread_id,
                )
            )
            await s.commit()

        cps_before = await list_checkpoints(settings, thread_id)
        assert cps_before, "seeded checkpoints not visible"

        await delete_thread(settings, thread_id)

        cps_after = await list_checkpoints(settings, thread_id)
        assert cps_after == []

        async with factory() as s:
            stores = (
                await s.execute(select(Store).where(Store.thread_id == thread_id))
            ).scalars().all()
            assert stores == []
            summaries = (
                await s.execute(
                    select(ConversationSummary).where(
                        ConversationSummary.thread_id == thread_id
                    )
                )
            ).scalars().all()
            assert summaries == []
        await engine.dispose()
    finally:
        # Idempotent — a successful delete above leaves nothing to clean up.
        await delete_thread(settings, thread_id)


# ---------- T507 ----------
@pytest.mark.asyncio
async def test_t507_parallel_threads_isolated() -> None:
    settings = _live_settings()
    a = _new_thread_id("t507-a")
    b = _new_thread_id("t507-b")
    try:
        await _seed_checkpoints(settings, a, n=2)
        await _seed_checkpoints(settings, b, n=3)
        cps_a = await list_checkpoints(settings, a)
        cps_b = await list_checkpoints(settings, b)
        assert len(cps_a) == 2
        assert len(cps_b) == 3
        ids_a = {c["checkpoint_id"] for c in cps_a}
        ids_b = {c["checkpoint_id"] for c in cps_b}
        assert ids_a.isdisjoint(ids_b), (
            "checkpoint id sets must not overlap between independent threads"
        )
    finally:
        await delete_thread(settings, a)
        await delete_thread(settings, b)


# ---------- T508 ----------
def test_t508_alembic_round_trip_creates_and_drops_agent_state() -> None:
    """The migration that adds agent_state (0003) is reversible. Re-applying
    head leaves the schema in its current state.
    """
    env = os.environ.copy()
    res_down = subprocess.run(
        ["uv", "run", "alembic", "downgrade", "0002"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_down.returncode == 0, (
        f"downgrade failed: stdout={res_down.stdout!r} stderr={res_down.stderr!r}"
    )
    res_up = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_up.returncode == 0, (
        f"upgrade failed: stdout={res_up.stdout!r} stderr={res_up.stderr!r}"
    )


# ---------- T510 ----------
@pytest.mark.asyncio
async def test_t510_checkpoint_count_grows_turn_over_turn(
    async_session: AsyncSession, mock_llm: MockChatModel
) -> None:
    from store_assistant.data.repository import (  # noqa: PLC0415
        StoreRepository,
        SummaryRepository,
    )
    from tests.conftest import make_save_call, make_text_response  # noqa: PLC0415

    settings = _live_settings()
    thread_id = _new_thread_id("t510")
    config = {"configurable": {"thread_id": thread_id}}
    try:
        store_repo = StoreRepository(async_session)
        summary_repo = SummaryRepository(async_session)
        mock_llm.queue(
            make_save_call("Counter Store", "919-555-0500"),
            make_text_response("Saved Counter Store."),
            make_save_call("Counter Store Two", "919-555-0501"),
            make_text_response("Saved Counter Store Two."),
        )
        async with production_checkpointer(settings) as (saver, _pool):
            graph = build_graph(
                settings, store_repo, summary_repo, llm=mock_llm, checkpointer=saver
            )
            await graph.ainvoke(
                {"messages": [HumanMessage("Save Counter Store, 919-555-0500")]},
                config=config,
            )
            after_turn_1 = len(await list_checkpoints(settings, thread_id))
            await graph.ainvoke(
                {"messages": [HumanMessage("Save Counter Store Two, 919-555-0501")]},
                config=config,
            )
            after_turn_2 = len(await list_checkpoints(settings, thread_id))
        assert after_turn_2 > after_turn_1
        assert after_turn_1 > 0
    finally:
        await delete_thread(settings, thread_id)


# ---------- T512 ----------
def test_t512_simulate_crash_cli_help_runs() -> None:
    """The simulate-crash CLI subcommand parses correctly. Actual SIGTERM
    delivery is exercised manually during the demo and isn't suitable for
    pytest (would need a real worker pid)."""
    result = subprocess.run(
        [sys.executable, "scripts/checkpoint_admin.py", "simulate-crash", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--pid" in result.stdout
    assert "--pidfile" in result.stdout
