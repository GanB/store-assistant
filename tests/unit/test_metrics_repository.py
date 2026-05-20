"""T604: session_totals aggregates correctly across multiple turns."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.data.repository import MetricsRepository
from store_assistant.metrics import ToolCallSummary, TurnMetrics


def _metrics(
    thread_id: str,
    idx: int,
    *,
    in_tok: int,
    out_tok: int,
    cost: str,
    latency: int,
) -> TurnMetrics:
    return TurnMetrics(
        turn_index=idx,
        thread_id=thread_id,
        checkpoint_id="",
        node_name="llm_node",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=Decimal(cost),
        latency_ms=latency,
        tool_calls=[],
        langsmith_run_id=None,
    )


@pytest.mark.asyncio
async def test_t604_session_totals_aggregates_across_turns(
    async_session: AsyncSession,
) -> None:
    repo = MetricsRepository(async_session)
    thread = "thread-totals"
    other = "other-thread"
    await repo.record_turn(
        _metrics(thread, 1, in_tok=100, out_tok=10, cost="0.000500", latency=1000)
    )
    await repo.record_turn(
        _metrics(thread, 2, in_tok=200, out_tok=20, cost="0.001000", latency=2000)
    )
    await repo.record_turn(
        _metrics(thread, 3, in_tok=50, out_tok=5, cost="0.000250", latency=900)
    )
    # Noise on a different thread — must not be aggregated.
    await repo.record_turn(
        _metrics(other, 1, in_tok=999, out_tok=999, cost="9.999999", latency=9999)
    )

    totals = await repo.session_totals(thread)
    assert totals["turns"] == 3
    assert totals["total_input_tokens"] == 350
    assert totals["total_output_tokens"] == 35
    assert totals["total_cost_usd"] == Decimal("0.001750")
    assert totals["avg_latency_ms"] == 1300


@pytest.mark.asyncio
async def test_t604_session_totals_empty_thread_returns_zeros(
    async_session: AsyncSession,
) -> None:
    repo = MetricsRepository(async_session)
    totals = await repo.session_totals("nonexistent")
    assert totals["turns"] == 0
    assert totals["total_input_tokens"] == 0


@pytest.mark.asyncio
async def test_record_turn_persists_tool_call_arg_keys_only(
    async_session: AsyncSession,
) -> None:
    """Sanity: tool_calls JSONB carries arg_keys, never arg values.

    Used as a precursor for T608 — the integration counterpart that drives
    the full agent loop. Here we exercise the repo write path directly."""
    repo = MetricsRepository(async_session)
    tc = ToolCallSummary(
        tool_name="get_store_phone",
        arg_keys=["name", "passphrase"],
        duration_ms=0,
        success=True,
    )
    metrics = TurnMetrics(
        turn_index=1,
        thread_id="t-args",
        checkpoint_id="",
        node_name="llm_node",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=Decimal("0.000050"),
        latency_ms=42,
        tool_calls=[tc],
        langsmith_run_id=None,
    )
    await repo.record_turn(metrics)
    rows = await repo.list_for_thread("t-args")
    assert len(rows) == 1
    persisted_tool_calls = rows[0].tool_calls
    assert persisted_tool_calls and isinstance(persisted_tool_calls, list)
    record = persisted_tool_calls[0]
    assert record["arg_keys"] == ["name", "passphrase"]
    assert "arg_values" not in record
    # No literal value-shaped keys were stored.
    for k in record:
        assert "value" not in k.lower()
